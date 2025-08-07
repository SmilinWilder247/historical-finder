import streamlit as st
import requests
import sqlite3
import json
import hashlib
import re
from datetime import datetime, timedelta
import pandas as pd
import stripe
import os

# Security configurations
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_YOUR_KEY_HERE")
st.set_page_config(page_title="Historical Truth Finder", page_icon="🔍", layout="wide")

# Input validation
def sanitize_input(text):
    if not text or len(text) > 200:
        return ""
    return re.sub(r'[^\w\s-]', '', text.strip())

def validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Secure database operations
def get_db_connection():
    conn = sqlite3.connect('searches.db', check_same_thread=False)
    conn.execute('PRAGMA foreign_keys = ON')
    return conn

def init_database():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY,
            query TEXT NOT NULL,
            user_hash TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            results_count INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS premium_users (
            id INTEGER PRIMARY KEY,
            user_hash TEXT UNIQUE NOT NULL,
            activated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL
        )
    ''')
    conn.commit()
    return conn

# User session management
def get_user_hash():
    if 'user_id' not in st.session_state:
        st.session_state.user_id = hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:16]
    return st.session_state.user_id

def check_premium_status():
    user_hash = get_user_hash()
    conn = get_db_connection()
    result = conn.execute(
        "SELECT expires_at FROM premium_users WHERE user_hash = ? AND expires_at > datetime('now')",
        (user_hash,)
    ).fetchone()
    conn.close()
    return result is not None

def add_premium_user(duration_days=30):
    user_hash = get_user_hash()
    expires_at = datetime.now() + timedelta(days=duration_days)
    conn = get_db_connection()
    conn.execute(
        "INSERT OR REPLACE INTO premium_users (user_hash, expires_at) VALUES (?, ?)",
        (user_hash, expires_at.isoformat())
    )
    conn.commit()
    conn.close()

# Rate limiting
def check_rate_limit():
    user_hash = get_user_hash()
    conn = get_db_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM searches WHERE user_hash = ? AND timestamp > datetime('now', '-24 hours')",
        (user_hash,)
    ).fetchone()[0]
    conn.close()
    return count

# Secure API calls
def safe_api_request(url, params, timeout=10):
    try:
        headers = {'User-Agent': 'HistoricalTruthFinder/1.0'}
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Network error: {type(e).__name__}")
        return None
    except json.JSONDecodeError:
        st.error("Invalid response format")
        return None

def analyze_with_huggingface(title, content):
    try:
        API_URL = "https://api-inference.huggingface.co/models/cardiffnlp/twitter-roberta-base-sentiment-latest"
        text = f"{sanitize_input(title)} {sanitize_input(content)}"[:500]
        
        response = requests.post(
            API_URL, 
            json={"inputs": text},
            headers={'User-Agent': 'HistoricalTruthFinder/1.0'},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            if result and len(result) > 0:
                label = result[0].get('label', 'NEUTRAL')
                score = min(max(result[0].get('score', 0.5), 0), 1)  # Clamp score
                
                return f"""🤖 AI Analysis:
- Document Sentiment: {label}
- Confidence: {score:.2f}
- Research Value: {"High" if score > 0.8 else "Medium"}
- Status: {"Potentially significant" if score > 0.7 else "Standard document"}"""
        
        return "AI analysis temporarily unavailable"
    except Exception as e:
        return f"Analysis error: {type(e).__name__}"

def create_stripe_session():
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Historical Truth Finder Premium',
                        'description': 'Monthly access to unlimited searches and AI analysis'
                    },
                    'unit_amount': 399,  # $3.99
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{st.secrets.get("BASE_URL", "http://localhost:8501")}/?premium=activated',
            cancel_url=f'{st.secrets.get("BASE_URL", "http://localhost:8501")}/',
            metadata={'user_hash': get_user_hash()}
        )
        return session.url
    except Exception as e:
        st.error(f"Payment system temporarily unavailable")
        return None

def calculate_suppression_index(docs, query):
    if not docs:
        return 0
    
    score = 0
    gov_keywords = ['classified', 'fbi', 'cia', 'government', 'secret', 'redacted']
    gov_sources = sum(1 for doc in docs if any(term in str(doc.get('title', '')).lower() for term in gov_keywords))
    score += min(gov_sources * 2, 6)
    
    old_docs = sum(1 for doc in docs if str(doc.get('date', '9999'))[:4] < '1980')
    score += min(old_docs * 3, 4)
    
    return min(score, 10)

# Main application
def main():
    st.title("🔍 Historical Truth Finder")
    st.caption("Professional research across 125+ years of archives")
    
    # Initialize database
    init_database()
    
    # Check premium status
    premium = check_premium_status() or st.query_params.get('premium') == 'activated'
    if st.query_params.get('premium') == 'activated':
        add_premium_user()
        st.success("✅ Premium activated!")
    
    # Sidebar
    with st.sidebar:
        st.header("🚀 Premium Features")
        st.write("• AI document analysis")
        st.write("• Unlimited daily searches")
        st.write("• Export functionality")
        st.write("• Advanced timeline analytics")
        
        if not premium:
            if st.button("💎 Upgrade - $3.99/month"):
                payment_url = create_stripe_session()
                if payment_url:
                    st.markdown(f"[Complete Payment]({payment_url})")
        else:
            st.success("✅ Premium Active")
    
    # Search interface
    col1, col2 = st.columns([3, 1])
    
    with col1:
        query = st.text_input("🔍 Research Topic:", placeholder="JFK assassination, MK-Ultra, etc.", max_chars=200)
    
    with col2:
        st.write("")
        st.write("")
        search_button = st.button("🚀 Search", type="primary")
    
    # Rate limiting for free users
    search_count = check_rate_limit()
    if not premium and search_count >= 5:
        st.warning("⏰ Daily limit reached (5 searches). Upgrade for unlimited access.")
        search_button = False
    
    if search_button and query:
        sanitized_query = sanitize_input(query)
        if not sanitized_query:
            st.error("Invalid search query")
            return
        
        with st.spinner("Searching archives..."):
            # Archive.org search with security
            params = {
                'q': f'{sanitized_query} AND mediatype:(texts OR data)',
                'output': 'json',
                'rows': 20 if premium else 10,
                'sort[]': 'date asc'
            }
            
            data = safe_api_request("https://archive.org/advancedsearch.php", params, timeout=15)
            
            if data:
                docs = data.get('response', {}).get('docs', [])
                
                # Log search securely
                conn = get_db_connection()
                conn.execute(
                    "INSERT INTO searches (query, user_hash, results_count) VALUES (?, ?, ?)",
                    (sanitized_query, get_user_hash(), len(docs))
                )
                conn.commit()
                conn.close()
                
                if docs:
                    # Premium analytics
                    if premium:
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.metric("🚨 Suppression Index", f"{calculate_suppression_index(docs, sanitized_query)}/10")
                        
                        with col2:
                            dates = [d.get('date', '')[:4] for d in docs if d.get('date', '')[:4].isdigit()]
                            if dates:
                                st.metric("📅 Date Range", f"{min(dates)} - {max(dates)}")
                        
                        with col3:
                            gov_count = sum(1 for d in docs if any(term in str(d.get('title', '')).lower() for term in ['government', 'fbi', 'cia']))
                            st.metric("🏛️ Gov Sources", gov_count)
                    
                    # Timeline visualization
                    st.subheader("📈 Timeline Distribution")
                    timeline_data = []
                    for doc in docs:
                        date = doc.get('date', '')
                        if date and len(date) >= 4 and date[:4].isdigit():
                            timeline_data.append({'Year': int(date[:4]), 'Count': 1})
                    
                    if timeline_data:
                        df = pd.DataFrame(timeline_data)
                        chart_data = df.groupby('Year')['Count'].sum().reset_index()
                        st.bar_chart(chart_data.set_index('Year'))
                    
                    # AI Analysis (Premium only)
                    if premium and docs:
                        st.subheader("🤖 AI Document Analysis")
                        top_doc = docs[0]
                        analysis = analyze_with_huggingface(
                            str(top_doc.get('title', '')),
                            str(top_doc.get('description', ''))
                        )
                        st.info(analysis)
                    
                    # Results display
                    st.subheader(f"📄 Historical Documents ({len(docs)} found)")
                    display_limit = 15 if premium else 5
                    
                    for doc in docs[:display_limit]:
                        title = str(doc.get('title', 'Untitled'))[:80]
                        date = str(doc.get('date', 'Unknown'))
                        
                        with st.expander(f"📋 {title}... ({date})"):
                            st.write(f"**Date:** {date}")
                            
                            description = str(doc.get('description', ''))
                            if description:
                                st.write(f"**Summary:** {description[:300]}...")
                            
                            identifier = str(doc.get('identifier', ''))
                            if identifier and re.match(r'^[a-zA-Z0-9_-]+$', identifier):
                                st.link_button("📖 View", f"https://archive.org/details/{identifier}")
                    
                    # Export (Premium)
                    if premium:
                        export_data = {
                            'query': sanitized_query,
                            'timestamp': datetime.now().isoformat(),
                            'total_results': len(docs),
                            'user_hash': get_user_hash()[:8],
                            'documents': [
                                {
                                    'title': str(d.get('title', '')),
                                    'date': str(d.get('date', '')),
                                    'url': f"https://archive.org/details/{d.get('identifier', '')}" if d.get('identifier') else None
                                } for d in docs[:10]
                            ]
                        }
                        st.download_button(
                            "💾 Export Report",
                            data=json.dumps(export_data, indent=2),
                            file_name=f"research_{sanitized_query.replace(' ', '_')}.json",
                            mime="application/json"
                        )
                
                else:
                    st.warning("No documents found. Try different keywords.")
            else:
                st.error("Search unavailable. Please try again.")
    
    # Additional sources
    if query and sanitize_input(query):
        safe_query = sanitize_input(query).replace(' ', '%20')
        st.subheader("🔗 Additional Sources")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.write("**🏛️ Government Archives**")
            st.write(f"[CIA Reading Room](https://www.cia.gov/readingroom/search/site/{safe_query})")
            st.write(f"[FBI Records](https://vault.fbi.gov/search?SearchableText={safe_query})")
        
        with col2:
            st.write("**📰 Historical News**")
            st.write(f"[Chronicling America](https://chroniclingamerica.loc.gov/search/pages/results/?andtext={safe_query})")
        
        with col3:
            st.write("**🔍 Alternative Sources**")
            st.write(f"[WikiLeaks](https://search.wikileaks.org/advanced?q={safe_query})")
    
    # Footer
    st.markdown("---")
    st.caption("© 2025 Historical Truth Finder | [Support](mailto:support@yourapp.com) | [Privacy](https://yourapp.com/privacy)")

if __name__ == "__main__":
    main()

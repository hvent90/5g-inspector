"""
Service Terms Documentation Module

This module manages the documentation of T-Mobile advertised speeds and service terms
for use in FCC complaint preparation.
"""

import json
import os
import threading
from datetime import datetime

# File path for persistent storage
SERVICE_TERMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'service_terms.json')

# Default service terms structure with user-reported advertised speeds
service_terms = {
    'plan_name': '',
    'monthly_cost': None,
    # Advertised speeds (user reported from T-Mobile website)
    'advertised_download_min': 133,
    'advertised_download_max': 415,
    'advertised_upload_min': 12,
    'advertised_upload_max': 55,
    'advertised_latency_min': 16,
    'advertised_latency_max': 28,
    # Service details
    'service_start_date': None,
    'service_address': '',
    'account_number': '',
    # Terms and policies
    'contract_terms': '',
    'deprioritization_policy': '',
    'data_cap_policy': '',
    'throttling_terms': '',
    'typical_language': '',  # Note any "typical" or "up to" language
    'promotional_claims': '',
    # Evidence documentation
    'website_screenshot_date': None,
    'screenshot_url': '',
    'terms_of_service_date': None,
    'notes': '',
    'updated_at': None
}

service_terms_lock = threading.Lock()


def load_service_terms():
    """Load service terms from file"""
    global service_terms
    try:
        if os.path.exists(SERVICE_TERMS_FILE):
            with open(SERVICE_TERMS_FILE, 'r') as f:
                loaded = json.load(f)
                service_terms.update(loaded)
                print(f'[SERVICE_TERMS] Loaded service terms documentation')
    except Exception as e:
        print(f'[SERVICE_TERMS] Error loading terms: {e}')


def save_service_terms():
    """Save service terms to file"""
    try:
        with service_terms_lock:
            with open(SERVICE_TERMS_FILE, 'w') as f:
                json.dump(service_terms, f, indent=2)
    except Exception as e:
        print(f'[SERVICE_TERMS] Error saving terms: {e}')


def get_service_terms():
    """Get current service terms"""
    with service_terms_lock:
        return service_terms.copy()


def update_service_terms(updates):
    """Update service terms documentation"""
    global service_terms
    allowed_fields = [
        'plan_name', 'monthly_cost',
        'advertised_download_min', 'advertised_download_max',
        'advertised_upload_min', 'advertised_upload_max',
        'advertised_latency_min', 'advertised_latency_max',
        'service_start_date', 'service_address', 'account_number',
        'contract_terms', 'deprioritization_policy',
        'data_cap_policy', 'throttling_terms',
        'typical_language', 'promotional_claims',
        'website_screenshot_date', 'screenshot_url',
        'terms_of_service_date', 'notes'
    ]
    with service_terms_lock:
        for field in allowed_fields:
            if field in updates:
                service_terms[field] = updates[field]
        service_terms['updated_at'] = datetime.utcnow().isoformat()
    save_service_terms()
    print(f'[SERVICE_TERMS] Updated service terms')
    return service_terms.copy()


def get_service_terms_summary():
    """Get summary for FCC complaint"""
    with service_terms_lock:
        terms = service_terms.copy()

    summary = {
        'terms': terms,
        'advertised_speeds': {
            'download': f"{terms.get('advertised_download_min', 133)}-{terms.get('advertised_download_max', 415)} Mbps",
            'upload': f"{terms.get('advertised_upload_min', 12)}-{terms.get('advertised_upload_max', 55)} Mbps",
            'latency': f"{terms.get('advertised_latency_min', 16)}-{terms.get('advertised_latency_max', 28)} ms"
        },
        'documentation_complete': bool(
            terms.get('plan_name') and
            terms.get('monthly_cost') and
            terms.get('service_start_date')
        ),
        'has_policy_documentation': bool(
            terms.get('deprioritization_policy') or
            terms.get('data_cap_policy') or
            terms.get('throttling_terms')
        ),
        'has_evidence': bool(
            terms.get('website_screenshot_date') or
            terms.get('terms_of_service_date')
        )
    }

    return summary


def get_fcc_export():
    """Export service terms in FCC complaint format"""
    with service_terms_lock:
        terms = service_terms.copy()

    export = {
        'service_information': {
            'provider': 'T-Mobile',
            'service_type': 'Home Internet',
            'plan_name': terms.get('plan_name', 'Not documented'),
            'monthly_cost': terms.get('monthly_cost'),
            'service_start_date': terms.get('service_start_date'),
            'service_address': terms.get('service_address', 'Not documented'),
            'account_number': terms.get('account_number', 'Not documented'),
        },
        'advertised_performance': {
            'download_speed': f"{terms.get('advertised_download_min', 133)}-{terms.get('advertised_download_max', 415)} Mbps",
            'upload_speed': f"{terms.get('advertised_upload_min', 12)}-{terms.get('advertised_upload_max', 55)} Mbps",
            'latency': f"{terms.get('advertised_latency_min', 16)}-{terms.get('advertised_latency_max', 28)} ms",
            'typical_language_used': terms.get('typical_language', 'Not documented'),
            'promotional_claims': terms.get('promotional_claims', 'Not documented'),
        },
        'policies': {
            'deprioritization': terms.get('deprioritization_policy', 'Not documented'),
            'data_cap': terms.get('data_cap_policy', 'Not documented'),
            'throttling': terms.get('throttling_terms', 'Not documented'),
            'contract_terms': terms.get('contract_terms', 'Not documented'),
        },
        'evidence_documentation': {
            'website_screenshot_date': terms.get('website_screenshot_date'),
            'screenshot_url': terms.get('screenshot_url'),
            'terms_of_service_date': terms.get('terms_of_service_date'),
        },
        'notes': terms.get('notes', ''),
        'last_updated': terms.get('updated_at'),
    }

    return export


# Initialize on module load
load_service_terms()

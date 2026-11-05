#!/usr/bin/env python
"""Test Odoo connection and CRM lead creation"""
import os
import sys
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

ODOO_URL = os.environ.get('ODOO_URL', '').rstrip('/')
ODOO_DB = os.environ.get('ODOO_DB', '')
ODOO_USERNAME = os.environ.get('ODOO_USERNAME', '')
ODOO_API_KEY = os.environ.get('ODOO_API_KEY', '')

print('='*50)
print('ODOO CONNECTION TEST')
print('='*50)
print(f'URL: {ODOO_URL}')
print(f'DB: {ODOO_DB}')
print(f'User: {ODOO_USERNAME}')
print()

try:
    # Test 1: Check version
    print('1. Testing server connection...')
    common = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/common')
    version = common.version()
    print(f'   ✅ Odoo Version: {version.get("server_version", "unknown")}')
    
    # Test 2: Authenticate
    print('\n2. Testing authentication...')
    uid = common.authenticate(ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {})
    if uid:
        print(f'   ✅ Authentication successful! User ID: {uid}')
    else:
        print('   ❌ Authentication failed - check credentials')
        sys.exit(1)
    
    # Test 3: Create a test lead
    print('\n3. Creating test CRM lead...')
    models = xmlrpc.client.ServerProxy(f'{ODOO_URL}/xmlrpc/2/object')
    
    test_lead = {
        'name': 'TEST — Bot Connection Test',
        'partner_name': 'Test Company',
        'type': 'opportunity',
        'expected_revenue': 50000,
        'description': 'This is a test lead created by the POWR WhatsApp Bot to verify Odoo integration.',
    }
    
    lead_id = models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        'crm.lead', 'create',
        [test_lead]
    )
    print(f'   ✅ Test lead created! ID: {lead_id}')
    
    # Test 4: Read back the lead
    print('\n4. Reading back the lead...')
    leads = models.execute_kw(
        ODOO_DB, uid, ODOO_API_KEY,
        'crm.lead', 'read',
        [[lead_id], ['name', 'partner_name', 'expected_revenue']]
    )
    if leads:
        print(f'   ✅ Lead data: {leads[0]}')
    
    print('\n' + '='*50)
    print('🎉 ALL TESTS PASSED! Odoo is working correctly!')
    print('='*50)
    print(f'\nYou can view the lead at:')
    print(f'{ODOO_URL}/web#id={lead_id}&model=crm.lead&view_type=form')
    
except Exception as e:
    print(f'\n❌ Error: {e}')
    sys.exit(1)

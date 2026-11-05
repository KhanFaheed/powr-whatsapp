#!/usr/bin/env python
"""
POWR WhatsApp Bot - Dev Test CLI
Usage:
    python dev_test.py              # Interactive mode
    python dev_test.py --test-all   # Run all three test cases
    python dev_test.py --test A     # Run test case A only
"""
import os
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from assistant.services.agent import handle_message
from assistant.models import ConversationState

# ANSI colors
class C:
    H = '\033[95m'; B = '\033[94m'; CY = '\033[96m'; G = '\033[92m'
    Y = '\033[93m'; R = '\033[91m'; E = '\033[0m'; BD = '\033[1m'

def hdr(t): print(f"\n{C.H}{C.BD}{'='*60}\n{t:^60}\n{'='*60}{C.E}\n")
def usr(t): print(f"{C.CY}👤 User:{C.E} {t}")
def bot(t): print(f"{C.G}🤖 Bot:{C.E}"); [print(f"   {l}") for l in t.split('\n')]
def info(t): print(f"{C.Y}ℹ️  {t}{C.E}")
def ok(t): print(f"{C.G}✅ {t}{C.E}")
def err(t): print(f"{C.R}❌ {t}{C.E}")
def clear(ph): ConversationState.objects.filter(phone=ph).delete()

def test_a():
    """Test A: Complete request - brand, platform, duration, budget"""
    hdr("TEST A: Complete Request")
    info("Scenario: Brand + platform + duration + budget")
    info("Expected: Odoo opportunity + matching quote\n")
    
    ph = "whatsapp:+966500000001"
    clear(ph)
    
    msg = "مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال."
    usr(msg)
    print()
    
    try:
        r = handle_message(ph, msg)
        bot(r)
        if "عرض السعر" in r or "مرجع:" in r:
            print(); ok("TEST A PASSED: Quote + Odoo reference")
        else:
            print(); info("Check response manually")
    except Exception as e:
        err(f"Error: {e}")
    return True

def test_b():
    """Test B: Incomplete request - missing platform/budget"""
    hdr("TEST B: Incomplete Request")
    info("Scenario: Missing platform and budget")
    info("Expected: Bot asks questions, doesn't invent values\n")
    
    ph = "whatsapp:+966500000002"
    clear(ph)
    
    msg1 = "السلام عليكم، نحن من شركة STC نريد إعلان"
    usr(msg1)
    print()
    
    try:
        r1 = handle_message(ph, msg1)
        bot(r1)
        if "المنصة" in r1 or "نوع" in r1:
            print(); ok("Bot asks for missing info")
        
        print("\n" + "-"*40 + "\n")
        
        msg2 = "نريده على إنستقرام ريل"
        usr(msg2)
        print()
        
        r2 = handle_message(ph, msg2)
        bot(r2)
        if "عرض السعر" in r2 or "مرجع:" in r2:
            print(); ok("TEST B PASSED: Collected info then quoted")
    except Exception as e:
        err(f"Error: {e}")
    return True

def test_c():
    """Test C: Excessive discount - above allowed limit"""
    hdr("TEST C: Excessive Discount")
    info("Scenario: 30% discount (exceeds 10% limit for YT-INT-A)")
    info("Expected: Escalated to sales, no price promised\n")
    
    ph = "whatsapp:+966500000003"
    clear(ph)
    
    msg = "مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%."
    usr(msg)
    print()
    
    try:
        r = handle_message(ph, msg)
        bot(r)
        if "فريق المبيعات" in r or "الحد الأقصى" in r or "تحويل" in r:
            print(); ok("TEST C PASSED: Escalated to sales team")
    except Exception as e:
        err(f"Error: {e}")
    return True

def interactive():
    """Interactive chat mode"""
    hdr("Interactive Mode")
    info("Type messages in Arabic/English")
    info("'reset' = clear state, 'quit' = exit\n")
    
    ph = "whatsapp:+966500000000"
    
    while True:
        try:
            inp = input(f"{C.CY}You: {C.E}").strip()
            if not inp: continue
            if inp.lower() in ['quit', 'exit', 'q']: break
            if inp.lower() == 'reset':
                clear(ph); info("State cleared"); continue
            
            r = handle_message(ph, inp)
            print(); bot(r); print()
        except KeyboardInterrupt:
            break
        except Exception as e:
            err(f"Error: {e}")

def main():
    p = argparse.ArgumentParser(description="POWR Bot Dev Tester")
    p.add_argument('--test', choices=['A', 'B', 'C', 'a', 'b', 'c'])
    p.add_argument('--test-all', action='store_true')
    p.add_argument('--no-pause', action='store_true', help='Skip pauses between tests')
    args = p.parse_args()
    
    if args.test_all:
        hdr("Running All Tests")
        test_a()
        if not args.no_pause:
            try: input(f"{C.Y}Press Enter for Test B...{C.E}")
            except EOFError: pass
        print()
        test_b()
        if not args.no_pause:
            try: input(f"{C.Y}Press Enter for Test C...{C.E}")
            except EOFError: pass
        print()
        test_c()
        hdr("All Tests Complete")
    elif args.test:
        {'A': test_a, 'B': test_b, 'C': test_c}[args.test.upper()]()
    else:
        interactive()

if __name__ == "__main__":
    main()

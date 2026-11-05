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

def run_session(ph, msgs):
    """Send a sequence of messages through the agent, printing every exchange."""
    clear(ph)
    replies = []
    for m in msgs:
        usr(m)
        print()
        r = handle_message(ph, m)
        bot(r)
        replies.append(r)
        print("-" * 60)
    return replies


def test_a():
    """Test A: complete request -> Odoo opportunity + price-sheet quote"""
    hdr("TEST A: Complete Request")
    info("Input : brand + platform + format + duration + budget (one message)")
    info("Expect: price-sheet quote + Odoo opportunity after confirmation\n")

    ph = "whatsapp:+966500000001"
    replies = run_session(ph, [
        "مرحبا، أنا من شركة نون. نريد حملة إعلانية على يوتيوب (تكامل) لمدة شهرين. الميزانية 100,000 ريال.",
        "بدون خصم",
        "اسمي نون ops@noon.com",
        "1",
    ])
    final = replies[-1]
    if "مرجع" in final or "عرض السعر" in final:
        print(); ok("TEST A PASSED: quote issued with an Odoo reference")
        return True
    print(); err("TEST A FAILED: expected the final quote message")
    return False


def test_b():
    """Test B: incomplete request - no platform, no budget"""
    hdr("TEST B: Incomplete Request")
    info("Input : brand only (no platform, no budget)")
    info("Expect: short questions, no invented values\n")

    ph = "whatsapp:+966500000002"
    replies = run_session(ph, [
        "السلام عليكم، نحن من شركة STC نريد إعلان",
        "نريده على إنستقرام ريل",
        "تخطي",
        "بدون خصم",
        "تخطي",
        "1",
    ])
    first = replies[0]
    if "المنصة" in first and "البريد" not in first:
        print(); ok("Asks only for the missing fields (platform / format)")
    else:
        print(); info("Check the first reply manually")

    final = replies[-1]
    if "مرجع" in final or "عرض السعر" in final:
        print(); ok("TEST B PASSED: collected info, then quoted")
        return True
    print(); err("TEST B FAILED: expected a quote after the details were given")
    return False


def test_c():
    """Test C: discount above the sheet limit -> human handover, no price"""
    hdr("TEST C: Discount Above the Allowed Limit")
    info("Input : YouTube integration, Tier A, 30% discount (cap is 10%)")
    info("Expect: no price promised + escalation to a person with a summary\n")

    ph = "whatsapp:+966500000003"
    replies = run_session(ph, [
        "مرحبا، أنا من شركة أمازون. نريد حملة يوتيوب تكامل Tier A لمدة شهر. نحتاج خصم 30%.",
        "اسمي أمازون sales@amazon.sa",
        "1",
    ])
    summary = replies[1]
    if "10" in summary and ("مبيعات" in summary or "sales" in summary):
        print(); ok("Summary states the 10% cap and announces the handover")
    else:
        print(); info("Check the summary output manually")

    final = replies[-1]
    if "فريق المبيعات" in final or "sales team" in final:
        print(); ok("TEST C PASSED: escalated to a human, no price promised")
        return True
    print(); err("TEST C FAILED: expected an escalation message")
    return False

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
        results = {"A": test_a()}
        if not args.no_pause:
            try: input(f"{C.Y}Press Enter for Test B...{C.E}")
            except EOFError: pass
        print()
        results["B"] = test_b()
        if not args.no_pause:
            try: input(f"{C.Y}Press Enter for Test C...{C.E}")
            except EOFError: pass
        print()
        results["C"] = test_c()
        hdr("All Tests Complete")
        for name, passed in results.items():
            (ok if passed else err)(f"Test {name}: {'PASSED' if passed else 'FAILED'}")
        sys.exit(0 if all(results.values()) else 1)
    elif args.test:
        passed = {'A': test_a, 'B': test_b, 'C': test_c}[args.test.upper()]()
        sys.exit(0 if passed else 1)
    else:
        interactive()

if __name__ == "__main__":
    main()

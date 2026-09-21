"""Stable presentation for persisted City Run events; no Discord connection needed."""
def event_style(key):
    kind = key.split(':', 1)[0]
    styles = {
        'set': ('Route Completed', 0x34D399),
        'full': ('Full Set Completed', 0xFBBF24),
        'grand': ('Grand Prize Completed', 0xF59E0B),
        'trade': ('Marketplace Trade Completed', 0x38BDF8),
        'error': ('System Error / Sticker Failed', 0xEF4444),
        'season-start': ('Season Started', 0x22C55E),
        'season-end': ('Season Ended', 0x94A3B8),
        'marketplace-admin': ('Marketplace Administration', 0xA78BFA),
    }
    if kind == 'claim':
        return ('Reward Issued', 0x22C55E) if ':fulfilled:' in key else ('Reward Cancelled', 0xF97316)
    return styles.get(kind, ('City Run Update', 0xFBBF24))

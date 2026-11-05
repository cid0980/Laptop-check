"""The one colour truth of Laptop Check Pro.

Three theme colours only - charcoal neutral, off-white ink, one blue accent -
plus semantic status colours reserved exclusively for verdicts (pass / warn /
fail), exactly as dashboard design systems prescribe. Every screen, dialog,
icon and the report derive from here.
"""
BG = "#0f172a"        # canvas (deep blue-charcoal, never pure black)
SURFACE = "#1e293b"   # cards / panels
RAISED = "#27364b"    # inputs, keycaps, hovered surfaces
LINE = "#334155"      # borders, dividers, tracks
TEXT = "#e2e8f0"      # off-white ink (never pure white)
MUTED = "#94a3b8"     # secondary ink
ACCENT = "#3b82f6"    # the single brand / action colour

GOOD = "#10b981"      # semantic: pass
OKAY = "#f59e0b"      # semantic: needs attention
BAD = "#ef4444"       # semantic: problem
NA = "#64748b"        # semantic: not fitted / not tested

# light theme (printed report / PDF)
L_BG = "#f8fafc"
L_CARD = "#ffffff"
L_LINE = "#e2e8f0"
L_INK = "#0f172a"
L_MUTED = "#64748b"
L_ACCENT = "#2563eb"

import sys, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
out = sys.argv[1]
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False})
BLUE, RED, GREEN, GREY = "#2196F3", "#D32F2F", "#2E8B57", "#555555"
a, b = -3, 5   # same a = -3 that P-384 uses, smaller b so it fits on a plot

# ---------- Figure 1: point addition over the real numbers ----------
def add(P, Q):
    (x1, y1), (x2, y2) = P, Q
    m = (3*x1*x1 + a)/(2*y1) if P == Q else (y2-y1)/(x2-x1)
    x3 = m*m - x1 - x2
    return (x3, m*(x1-x3) - y1), m

fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
xs = np.linspace(-2.28, 3.2, 4000)
ys2 = xs**3 + a*xs + b
ok = ys2 >= 0
ax.plot(xs[ok], np.sqrt(ys2[ok]), color=BLUE, lw=3)
ax.plot(xs[ok], -np.sqrt(ys2[ok]), color=BLUE, lw=3)
P = (-1.8, np.sqrt((-1.8)**3 + a*-1.8 + b))
Q = (0.0, np.sqrt(b))
R, m = add(P, Q)
negR = (R[0], -R[1])
lx = np.linspace(-2.3, R[0] + 0.3, 10)
ax.plot(lx, P[1] + m*(lx - P[0]), "--", color=GREY, lw=1.6)
ax.plot([R[0], R[0]], [-R[1], R[1]], ":", color=RED, lw=2)
for pt, name, col, off in [(P, "P", GREEN, (-10, 14)), (Q, "Q", GREEN, (-6, 14)),
                           (negR, "the line hits\nthe curve again", GREY, (14, 0)),
                           (R, "P + Q\n(flip across x-axis)", RED, (14, -14))]:
    ax.scatter(*pt, s=150, color=col, zorder=5)
    ax.annotate(name, pt, textcoords="offset points", xytext=off, fontsize=13, fontweight="bold", color=col)
ax.set_title("Adding two points: draw a line, find the third hit, flip it", fontsize=15, fontweight="bold", color="#333")
ax.set_xlabel("x", fontsize=13); ax.set_ylabel("y", fontsize=13)
ax.text(-2.2, 4.3, r"$y^2 = x^3 - 3x + 5$", fontsize=14, color=BLUE)
ax.set_ylim(-5, 5)
fig.tight_layout(); fig.savefig(f"{out}/ec_point_addition.png"); plt.close(fig)

# ---------- Figure 2: the same curve over a finite field ----------
p = 97
pts = [(x, y) for x in range(p) for y in range(p) if (y*y - (x**3 + a*x + b)) % p == 0]
def fadd(P, Q):
    if P is None: return Q
    (x1, y1), (x2, y2) = P, Q
    if x1 == x2 and (y1 + y2) % p == 0: return None
    m = (3*x1*x1 + a) * pow(2*y1, -1, p) if P == Q else (y2-y1) * pow(x2-x1, -1, p)
    x3 = (m*m - x1 - x2) % p
    return (x3, (m*(x1-x3) - y1) % p)
G = next(pt for pt in pts if pt[1] != 0)
mults, cur = [], None
for k in range(1, 7):
    cur = fadd(cur, G); mults.append(cur)
order, cur = 0, None
while True:
    cur = fadd(cur, G); order += 1
    if cur is None: break
print("points on curve:", len(pts) + 1, "| G =", G, "| order of G:", order, "| multiples:", mults)

fig, ax = plt.subplots(figsize=(9, 7), dpi=130)
ax.scatter(*zip(*pts), s=22, color=BLUE, alpha=0.55)
mx, my = zip(*mults)
ax.scatter(mx, my, s=140, color=RED, zorder=5)
for k, pt in enumerate(mults, 1):
    ax.annotate("G" if k == 1 else f"{k}G", pt, textcoords="offset points", xytext=(8, 6),
                fontsize=13, fontweight="bold", color=RED)
ax.set_title(f"The same curve with numbers mod {p}: G, 2G, 3G... jump around", fontsize=15, fontweight="bold", color="#333")
ax.set_xlabel("x  (mod 97)", fontsize=13); ax.set_ylabel("y  (mod 97)", fontsize=13)
ax.set_xlim(-2, 99); ax.set_ylim(-2, 104)
fig.tight_layout(); fig.savefig(f"{out}/ec_finite_field.png"); plt.close(fig)

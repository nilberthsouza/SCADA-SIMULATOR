"""Geradores de diagrama unifilar: arranjos de barra de AT + lado de MT."""
from __future__ import annotations

from .core import KV_HV, KV_LV, Sub

ARRANGEMENTS = {
    "single": "Barra simples",
    "transfer": "Barra principal e transferência",
    "dbl3": "Barra dupla a 3 chaves",
    "dbl4": "Barra dupla a 4 chaves (com by-pass)",
    "bk15": "Barra dupla – disjuntor e meio",
}

DESCRIPTIONS = {
    "single": ("Uma única barra. Simples e barata, porém qualquer manutenção em barra, seccionadora de barra "
               "ou disjuntor exige desligar o(s) vão(s) envolvido(s). Sem flexibilidade."),
    "transfer": ("Barra principal + barra de transferência. O disjuntor de transferência (52-TF) substitui o "
                 "disjuntor de qualquer vão via seccionadora de by-pass (89-xx-BP), permitindo manutenção de "
                 "disjuntor sem desligar a saída."),
    "dbl3": ("Duas barras, cada vão com 2 seccionadoras de barra (89-B1/89-B2), disjuntor e 1 seccionadora de "
             "saída, mais vão de acoplamento (52-AC). Permite transferir vãos entre barras (barra liberada para "
             "manutenção), mas a manutenção do disjuntor desliga o vão."),
    "dbl4": ("Como a barra dupla a 3 chaves, com a 4ª chave de by-pass (89-xx-BP) que liga o lado da saída à "
             "barra 2: o disjuntor de acoplamento passa a atuar como disjuntor do vão, liberando o disjuntor para "
             "manutenção."),
    "bk15": ("Disjuntor e meio: cada diâmetro tem 3 disjuntores em série entre as duas barras e dois circuitos "
             "derivados entre eles. Qualquer disjuntor ou barra pode ser retirado sem interromper circuitos."),
}


class Col:
    """Cursor de desenho que avança numa direção, criando fios e equipamentos em série."""

    ROT = {(0, 1): 0, (-1, 0): 90, (0, -1): 180, (1, 0): 270}

    def __init__(self, sub, scene, x, y, dirn, net, kv=KV_HV):
        self.sub, self.scene, self.x, self.y, self.d, self.net, self.kv = sub, scene, x, y, dirn, net, kv
        self.rot = self.ROT[dirn]

    def wire(self, length):
        x2, y2 = self.x + self.d[0] * length, self.y + self.d[1] * length
        self.sub.add_wire(self.scene, self.net, [(self.x, self.y), (x2, y2)])
        self.x, self.y = x2, y2

    def to(self, target):
        """Fio até a coordenada (x ou y, conforme a direção)."""
        cur = self.x if self.d[0] else self.y
        self.wire(abs(target - cur))

    def dev(self, kind, name, nb=None, gap=8, lab="R", closed=True, **kw):
        if gap:
            self.wire(gap)
        cx, cy = self.x + self.d[0] * 18, self.y + self.d[1] * 18
        if nb is None:
            nb = self.sub.new_net(f"Entre {name} e o próximo", self.kv)
        d = self.sub.add_dev(kind, name, self.net, nb, closed=closed, **kw)
        self.sub.place(d, self.scene, cx, cy, self.rot, lab)
        self.x, self.y = cx + self.d[0] * 18, cy + self.d[1] * 18
        self.net = nb
        return d

    def st(self, name, side=1, gap=14):
        self.wire(gap)
        px, py = self.d[1] * side, -self.d[0] * side
        bx, by = self.x + px * 34, self.y + py * 34
        self.sub.add_wire(self.scene, self.net, [(self.x, self.y), (bx, by)])
        self.sub.add_dot(self.scene, self.net, self.x, self.y)
        d = self.sub.add_dev("ST", name, self.net, None, closed=False)
        self.sub.place(d, self.scene, bx + self.d[0] * 18, by + self.d[1] * 18, self.rot, "R" if side > 0 else "L")
        return d

    def equip(self, c, lvnets):
        return make_equipment(self, c, lvnets)


def bus_st(sub, scene, net, x, y, name, up=True):
    d = sub.add_dev("ST", name, net, None, closed=False)
    if up:
        sub.place(d, scene, x, y - 18, 180, "R")
    else:
        sub.place(d, scene, x, y + 18, 0, "R")
    sub.add_dot(scene, net, x, y)


def make_equipment(col, c, lvnets):
    name, kind = c
    col.wire(10)
    cx, cy = col.x + col.d[0] * 18, col.y + col.d[1] * 18
    if kind == "TR":
        d = col.sub.add_dev("TR", name, col.net, lvnets[name], mva=25.0)
    else:
        d = col.sub.add_dev("LINE", name, col.net, None)
    col.sub.place(d, col.scene, cx, cy, col.rot, "D")
    return d


def interleave(nl, nt):
    L = [(f"LT{i + 1}", "LINE") for i in range(nl)]
    T = [(f"T{i + 1}", "TR") for i in range(nt)]
    out = []
    while L or T:
        if L:
            out.append(L.pop(0))
        if T:
            out.append(T.pop(0))
    return out


def bus_wire(sub, scene, net, x1, x2, y, label):
    sub.add_wire(scene, net, [(x1, y), (x2, y)], bus=True)
    sub.add_text(scene, net, x1 + 110, y - 38, label)


# ---------------------------------------------------------------- barra simples
def hv_single(sub, circs, lv):
    DX, x0, yb = 175, 150, 90
    xs = [x0 + i * DX for i in range(len(circs))]
    B1 = sub.new_net("BARRA 1 (138 kV)", KV_HV, bus=True)
    bus_wire(sub, "HV", B1, xs[0] - 90, xs[-1] + 70, yb, "BARRA 1 – 138 kV")
    bus_st(sub, "HV", B1, xs[0] - 90, yb, "89T-B1")
    for x, c in zip(xs, circs):
        n = c[0]
        sub.add_dot("HV", B1, x, yb)
        col = Col(sub, "HV", x, yb, (0, 1), B1)
        col.dev("DS", f"89-{n}-B", lab="L", gap=12)
        col.st(f"89T-{n}-1", +1)
        col.dev("DJ", f"52-{n}", lab="L")
        col.st(f"89T-{n}-2", -1)
        col.dev("DS", f"89-{n}-L", lab="R")
        col.st(f"89T-{n}-3", +1)
        col.equip(c, lv)


# ----------------------------------------------- barra principal e transferência
def hv_transfer(sub, circs, lv):
    DX, x0, yb, ybt = 190, 150, 90, 500
    n = len(circs)
    xs = [x0 + i * DX for i in range(n + 1)]
    B1 = sub.new_net("BARRA PRINCIPAL (138 kV)", KV_HV, bus=True)
    BT = sub.new_net("BARRA DE TRANSFERÊNCIA (138 kV)", KV_HV, bus=True)
    bus_wire(sub, "HV", B1, xs[0] - 90, xs[-1] + 90, yb, "BARRA PRINCIPAL – 138 kV")
    bus_wire(sub, "HV", BT, xs[0] - 90, xs[-1] + 90, ybt, "BARRA DE TRANSFERÊNCIA – 138 kV")
    sub.texts[-1] = (sub.texts[-1][0], BT, sub.texts[-1][2], ybt + 4, sub.texts[-1][4])
    bus_st(sub, "HV", B1, xs[0] - 90, yb, "89T-BP")
    bus_st(sub, "HV", BT, xs[0] - 90, ybt, "89T-BT", up=False)
    for x, c in zip(xs, circs):
        nm = c[0]
        sub.add_dot("HV", B1, x, yb)
        col = Col(sub, "HV", x, yb, (0, 1), B1)
        col.dev("DS", f"89-{nm}-B", lab="L", gap=12)
        col.st(f"89T-{nm}-1", +1)
        col.dev("DJ", f"52-{nm}", lab="L")
        col.st(f"89T-{nm}-2", +1)
        col.dev("DS", f"89-{nm}-L", lab="R")
        col.wire(16)
        n3 = col.net
        sub.add_dot("HV", n3, col.x, col.y)
        sub.add_wire("HV", n3, [(col.x, col.y), (x + 50, col.y)])
        bc = Col(sub, "HV", x + 50, col.y, (0, 1), n3)
        bc.dev("DS", f"89-{nm}-BP", nb=BT, gap=30, lab="R", closed=False)
        bc.to(ybt)
        sub.add_dot("HV", BT, x + 50, ybt)
        col.st(f"89T-{nm}-3", -1)
        col.equip(c, lv)
    # vão de transferência
    x = xs[-1]
    sub.add_dot("HV", B1, x, yb)
    col = Col(sub, "HV", x, yb, (0, 1), B1)
    col.dev("DS", "89-TF-B", lab="L", gap=12, closed=False)
    col.st("89T-TF-1", -1)
    col.dev("DJ", "52-TF", lab="L", closed=False)
    col.st("89T-TF-2", -1)
    col.dev("DS", "89-TF-T", nb=BT, lab="R", closed=False)
    col.to(ybt)
    sub.add_dot("HV", BT, x, ybt)
    sub.add_text("HV", B1, x - 40, 340, "VÃO DE TRANSFERÊNCIA")


# ------------------------------------------------------------------ barra dupla
def hv_double(sub, circs, lv, four):
    DX, x0, y1, y2 = 215, 160, 80, 118
    n = len(circs)
    xs = [x0 + i * DX for i in range(n + 1)]
    B1 = sub.new_net("BARRA 1 (138 kV)", KV_HV, bus=True)
    B2 = sub.new_net("BARRA 2 (138 kV)", KV_HV, bus=True)
    bus_wire(sub, "HV", B1, xs[0] - 300, xs[-1] + 100, y1, "BARRA 1 – 138 kV")
    bus_wire(sub, "HV", B2, xs[0] - 300, xs[-1] + 100, y2, "BARRA 2 – 138 kV")
    sub.texts[-1] = (sub.texts[-1][0], B2, sub.texts[-1][2], y2 + 2, sub.texts[-1][4])
    bus_st(sub, "HV", B1, xs[0] - 300, y1, "89T-B1")
    bus_st(sub, "HV", B2, xs[0] - 300, y2, "89T-B2", up=False)
    for i, (x, c) in enumerate(zip(xs, circs)):
        nm = c[0]
        on1 = (i % 2 == 0)
        n1 = sub.new_net(f"{nm} – entrada do disjuntor", KV_HV)
        # ramo barra 1 (esquerda)
        sub.add_dot("HV", B1, x - 26, y1)
        c1 = Col(sub, "HV", x - 26, y1, (0, 1), B1)
        c1.dev("DS", f"89-{nm}-B1", nb=n1, gap=50, lab="L", closed=on1)
        sub.add_wire("HV", n1, [(c1.x, c1.y), (c1.x, 200), (x, 200)])
        # ramo barra 2 (direita)
        sub.add_dot("HV", B2, x + 26, y2)
        c2 = Col(sub, "HV", x + 26, y2, (0, 1), B2)
        c2.dev("DS", f"89-{nm}-B2", nb=n1, gap=12, lab="R", closed=not on1)
        sub.add_wire("HV", n1, [(c2.x, c2.y), (c2.x, 200), (x, 200)])
        sub.add_dot("HV", n1, x, 200)
        col = Col(sub, "HV", x, 200, (0, 1), n1)
        col.st(f"89T-{nm}-1", +1, gap=18)
        col.dev("DJ", f"52-{nm}", lab="L")
        col.st(f"89T-{nm}-2", -1)
        col.dev("DS", f"89-{nm}-L", lab="R")
        col.wire(16)
        n3 = col.net
        if four:
            sub.add_dot("HV", n3, col.x, col.y)
            sub.add_wire("HV", n3, [(col.x, col.y), (x + 95, col.y)])
            bc = Col(sub, "HV", x + 95, col.y, (0, -1), n3)
            bc.dev("DS", f"89-{nm}-BP", nb=B2, gap=20, lab="R", closed=False)
            bc.to(y2)
            sub.add_dot("HV", B2, x + 95, y2)
        col.st(f"89T-{nm}-3", -1)
        col.equip(c, lv)
    # vão de acoplamento
    x = xs[-1]
    sub.add_dot("HV", B1, x, y1)
    col = Col(sub, "HV", x, y1, (0, 1), B1)
    col.dev("DS", "89-AC-B1", lab="L", gap=30)
    col.st("89T-AC-1", -1)
    col.dev("DJ", "52-AC", lab="L")
    col.st("89T-AC-2", -1)
    col.dev("DS", "89-AC-B2", nb=B2, lab="R")
    sub.add_wire("HV", B2, [(col.x, col.y), (col.x, col.y + 14), (x + 50, col.y + 14), (x + 50, y2)])
    sub.add_dot("HV", B2, x + 50, y2)
    sub.add_text("HV", B1, x - 50, col.y + 28, "VÃO DE ACOPLAMENTO")


# ------------------------------------------------------------- disjuntor e meio
def hv_bk15(sub, circs, lv):
    DXD, x0, y1, y2 = 470, 250, 80, 720
    diam = [circs[i:i + 2] for i in range(0, len(circs), 2)]
    B1 = sub.new_net("BARRA 1 (138 kV)", KV_HV, bus=True)
    B2 = sub.new_net("BARRA 2 (138 kV)", KV_HV, bus=True)
    xs = [x0 + i * DXD for i in range(len(diam))]
    bus_wire(sub, "HV", B1, xs[0] - 300, xs[-1] + 200, y1, "BARRA 1 – 138 kV")
    bus_wire(sub, "HV", B2, xs[0] - 300, xs[-1] + 200, y2, "BARRA 2 – 138 kV")
    sub.texts[-1] = (sub.texts[-1][0], B2, sub.texts[-1][2], y2 + 2, sub.texts[-1][4])
    bus_st(sub, "HV", B1, xs[0] - 300, y1, "89T-B1")
    bus_st(sub, "HV", B2, xs[0] - 300, y2, "89T-B2", up=False)
    for k, (x, cs) in enumerate(zip(xs, diam), start=1):
        nb = 3 if len(cs) == 2 else 2
        sub.add_dot("HV", B1, x, y1)
        col = Col(sub, "HV", x, y1, (0, 1), B1)
        sides = {1: (+1, +1), 2: (+1, -1), 3: (+1, -1)}
        for j in range(1, nb + 1):
            last = (j == nb)
            col.dev("DS", f"89-D{k}-{j}A", lab="L", gap=10)
            col.st(f"89T-D{k}-{j}a", sides[j][0], gap=12)
            col.dev("DJ", f"52-D{k}-{j}", lab="L")
            col.st(f"89T-D{k}-{j}b", sides[j][1], gap=12)
            col.dev("DS", f"89-D{k}-{j}B", nb=B2 if last else None, lab="L" if j == 1 else "R", gap=10)
            if not last:
                col.wire(36)
                jn = col.net
                sub.add_dot("HV", jn, col.x, col.y)
                c = cs[j - 1]
                dirn = (-1, 0) if j == 1 else (1, 0)
                tc = Col(sub, "HV", col.x, col.y, dirn, jn)
                tc.dev("DS", f"89-{c[0]}-L", lab="D", gap=24)
                tc.st(f"89T-{c[0]}-L", -1 if dirn[0] < 0 else 1, gap=12)
                tc.equip(c, lv)
                col.st(f"89T-D{k}-J{j}", +1 if j == 1 else -1, gap=14)
                col.wire(22)
        col.to(y2)
        sub.add_dot("HV", B2, x, y2)


# --------------------------------------------------------------------- MT 13,8 kV
def build_lv(sub, trs, lvnets):
    DX, y_top, y_bus = 500, 60, 310
    for k, (tn, _) in enumerate(trs, start=1):
        x = 230 + (k - 1) * DX
        tr = next(d for d in sub.devs.values() if d.kind == "TR" and d.name == tn)
        sub.place(tr, "LV", x, y_top, 0, "R")
        lv = lvnets[tn]
        bus = sub.new_net(f"BARRA 13,8 kV – SEÇÃO {k}", KV_LV, bus=True)
        sub.add_text("LV", bus, x - 150, y_bus - 22, f"BARRA 13,8 kV – SEÇÃO {k}")
        sub.add_wire("LV", bus, [(x - 160, y_bus), (x + 160, y_bus)], bus=True)
        bus_st(sub, "LV", bus, x + 100, y_bus, f"89T-MT{k}")
        col = Col(sub, "LV", x, y_top + 18, (0, 1), lv, KV_LV)
        col.st(f"89T-{tn}-MT", +1, gap=16)
        col.dev("DS", f"89-{tn}-M1", lab="L", gap=8)
        col.st(f"89T-{tn}-MA", +1, gap=12)
        col.dev("DJ", f"52-{tn}-M", lab="L")
        col.st(f"89T-{tn}-MB", +1, gap=12)
        col.dev("DS", f"89-{tn}-M2", nb=bus, lab="L", gap=8)
        sub.add_wire("LV", bus, [(col.x, col.y), (col.x, y_bus)])
        sub.add_dot("LV", bus, x, y_bus)
        for j, fx in enumerate((x - 70, x + 70), start=1):
            nm = f"F{k}{j}"
            sub.add_dot("LV", bus, fx, y_bus)
            fc = Col(sub, "LV", fx, y_bus, (0, 1), bus, KV_LV)
            fc.dev("DS", f"89-{nm}-B", lab="L", gap=14)
            fc.st(f"89T-{nm}-0", +1, gap=12)
            fc.dev("DJ", f"52-{nm}", lab="L")
            fc.st(f"89T-{nm}-1", -1, gap=12)
            fc.dev("DS", f"89-{nm}-L", lab="L", gap=8)
            fc.st(f"89T-{nm}-2", +1, gap=12)
            fc.wire(10)
            ld = sub.add_dev("LOAD", f"ALIM {nm}", fc.net, None, p_nom=5.4 - 0.9 * ((k + j) % 3), pf=0.9 + 0.02 * ((k * 3 + j) % 4))
            sub.place(ld, "LV", fc.x, fc.y + 18, 0, "D")
    # secionamentos de barra (tie)
    for k in range(1, len(trs)):
        bk = [n for n in sub.nets.values() if n.name == f"BARRA 13,8 kV – SEÇÃO {k}"][0]
        bn = [n for n in sub.nets.values() if n.name == f"BARRA 13,8 kV – SEÇÃO {k + 1}"][0]
        xa = 230 + (k - 1) * DX + 160
        # liga as duas seções por DS-DJ-DS na horizontal, acima da barra
        col = Col(sub, "LV", xa, y_bus, (1, 0), bk.id, KV_LV)
        col.dev("DS", f"89-TIE{k}-1", lab="U", gap=10)
        col.st(f"89T-TIE{k}-1", -1, gap=10)
        col.dev("DJ", f"52-TIE{k}", lab="U", closed=False, gap=6)
        col.st(f"89T-TIE{k}-2", -1, gap=10)
        col.dev("DS", f"89-TIE{k}-2", nb=bn.id, lab="U", gap=6)
        sub.add_wire("LV", bn.id, [(col.x, col.y), (xa + DX - 320, y_bus)])


def build(arr: str, nt: int, nl: int) -> Sub:
    sub = Sub(f"{ARRANGEMENTS[arr]} – {nt} TR / {nl} LT", arr, nt, nl)
    sub.description = DESCRIPTIONS[arr]
    circs = interleave(nl, nt)
    trs = [c for c in circs if c[1] == "TR"]
    lvnets = {tn: sub.new_net(f"{tn} – lado 13,8 kV", KV_LV) for tn, _ in trs}
    if arr == "single":
        hv_single(sub, circs, lvnets)
    elif arr == "transfer":
        hv_transfer(sub, circs, lvnets)
    elif arr == "dbl3":
        hv_double(sub, circs, lvnets, False)
    elif arr == "dbl4":
        hv_double(sub, circs, lvnets, True)
    else:
        hv_bk15(sub, circs, lvnets)
    build_lv(sub, trs, lvnets)
    return sub

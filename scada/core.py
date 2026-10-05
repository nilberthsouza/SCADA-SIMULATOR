"""Modelo da subestação, análise topológica/fluxo e motor de simulação."""
from __future__ import annotations

import csv
import datetime as dt
import math
import random
import time

import numpy as np

KV_HV, KV_LV = 138.0, 13.8
FLOW_EPS = 0.05          # MVA abaixo do qual uma seccionadora pode operar
TR_P0, TR_Q0 = 0.15, 0.6  # corrente de magnetização do transformador
G_SW, G_TR = 1000.0, 100.0
SWITCH_KINDS = ("DJ", "DS", "ST")

KIND_NAMES = {
    "DJ": "Disjuntor", "DS": "Seccionadora", "ST": "Seccionadora de terra",
    "TR": "Transformador", "LINE": "Linha de transmissão", "LOAD": "Alimentador",
}


class Net:
    __slots__ = ("id", "name", "kv", "bus", "v", "live")

    def __init__(self, id, name, kv, bus=False):
        self.id, self.name, self.kv, self.bus = id, name, kv, bus
        self.v = 0.0
        self.live = False


class Dev:
    def __init__(self, id, kind, name, a, b=None, closed=True, **kw):
        self.id, self.kind, self.name, self.a, self.b = id, kind, name, a, b
        self.closed = closed
        self.alive = True                  # LINE: tensão remota presente
        self.p_nom = kw.get("p_nom", 0.0)  # LOAD (MW)
        self.pf = kw.get("pf", 0.93)
        self.mva = kw.get("mva", 25.0)     # TR
        self.tap = 0
        self.tag = None                    # etiqueta de bloqueio (LOTO)
        self.tripped = False
        self.damaged = False
        self.stuck = False
        self.pending = None                # (estado desejado, instante)
        self.phase = random.random() * 6.28
        self.mult = 1.0
        self.overload_since = None
        self.reclose_at = None
        self.tries = 0
        self.meas = {}

    @property
    def label(self):
        return f"{KIND_NAMES[self.kind]} {self.name}"


class Sub:
    """Estrutura da subestação + desenho (fios, equipamentos posicionados)."""

    def __init__(self, title, arrangement, nt, nl):
        self.title, self.arrangement, self.nt, self.nl = title, arrangement, nt, nl
        self.nets: dict[str, Net] = {}
        self.devs: dict[str, Dev] = {}
        self.wires = []   # (scene, net, pts, bus)
        self.places = []  # (dev, scene, x, y, rot, lab)
        self.dots = []    # (scene, net, x, y)
        self.texts = []   # (scene, net, x, y, text)
        self.description = ""
        self._n = 0

    def new_net(self, name, kv, bus=False):
        self._n += 1
        nid = f"n{self._n}"
        self.nets[nid] = Net(nid, name, kv, bus)
        return nid

    def add_dev(self, kind, name, a, b=None, closed=True, **kw):
        did = f"d{len(self.devs) + 1}"
        d = Dev(did, kind, name, a, b, closed, **kw)
        self.devs[did] = d
        return d

    def place(self, dev, scene, x, y, rot=0, lab="R"):
        self.places.append((dev, scene, x, y, rot, lab))

    def add_wire(self, scene, net, pts, bus=False):
        self.wires.append((scene, net, pts, bus))

    def add_dot(self, scene, net, x, y):
        self.dots.append((scene, net, x, y))

    def add_text(self, scene, net, x, y, text):
        self.texts.append((scene, net, x, y, text))

    def by_kind(self, *kinds):
        return [d for d in self.devs.values() if d.kind in kinds]

    def initial_state(self):
        return {d.id: d.closed for d in self.devs.values() if d.kind in SWITCH_KINDS}


# --------------------------------------------------------------------------
# Análise
# --------------------------------------------------------------------------
class Res:
    pass


def analyze(sub: Sub, st: dict, loadp: dict | None = None, solve: bool = True) -> Res:
    nets = sub.nets
    parent = {n: n for n in nets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in sub.devs.values():
        if (d.kind in ("DJ", "DS") and st[d.id]) or d.kind == "TR":
            ra, rb = find(d.a), find(d.b)
            if ra != rb:
                parent[ra] = rb
    res = Res()
    res.root = {n: find(n) for n in nets}
    src = {res.root[d.a] for d in sub.devs.values() if d.kind == "LINE" and d.alive}
    res.src = src
    res.live = {n for n in nets if res.root[n] in src}
    grounded = {res.root[d.a] for d in sub.devs.values() if d.kind == "ST" and st[d.id]}
    res.grounded = grounded
    res.short = src & grounded
    res.P, res.line_p, res.phi = {}, {}, {}
    if not solve or not res.live:
        return res

    live_nets = list(res.live)
    idx = {n: i for i, n in enumerate(live_nets)}
    N = len(live_nets)
    L = np.zeros((N, N))
    edges = []
    for d in sub.devs.values():
        if d.kind in ("DJ", "DS"):
            if not st[d.id]:
                continue
            g = G_SW
        elif d.kind == "TR":
            g = G_TR
        else:
            continue
        if d.a in idx and d.b in idx:
            i, j = idx[d.a], idx[d.b]
            if i == j:
                continue
            L[i, i] += g
            L[j, j] += g
            L[i, j] -= g
            L[j, i] -= g
            edges.append((d, i, j, g))
    slack = {idx[d.a] for d in sub.devs.values() if d.kind == "LINE" and d.alive}
    Pl = np.zeros(N)
    Ql = np.zeros(N)
    for d in sub.devs.values():
        if d.a in idx:
            if d.kind == "LOAD":
                p = (loadp or {}).get(d.id, d.p_nom)
                i = idx[d.a]
                Pl[i] += p
                Ql[i] += p * math.tan(math.acos(d.pf))
            elif d.kind == "TR":
                Pl[idx[d.a]] += TR_P0
                Ql[idx[d.a]] += TR_Q0
    free = [i for i in range(N) if i not in slack]
    phi = np.zeros((N, 2))
    if free:
        Lr = L[np.ix_(free, free)]
        B = np.column_stack([Pl[free], Ql[free]])
        phi[free] = np.linalg.solve(Lr, B)
    out = np.zeros((N, 2))
    for d, i, j, g in edges:
        f = g * (phi[j] - phi[i])          # fluxo a -> b (P, Q)
        res.P[d.id] = (float(f[0]), float(f[1]))
        # a/b podem estar invertidos em relação a i/j? i=a, j=b sempre
        out[i] += f
        out[j] -= f
    for d in sub.devs.values():
        if d.kind == "LINE" and d.alive and d.a in idx:
            o = out[idx[d.a]]
            res.line_p[d.id] = (float(o[0]), float(o[1]))
    res.phi = {n: float(phi[idx[n]][0]) for n in live_nets}
    return res


def apparent(pq):
    return math.hypot(pq[0], pq[1])


def connected(sub: Sub, st: dict, a: str, b: str) -> bool:
    parent = {n: n for n in sub.nets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in sub.devs.values():
        if (d.kind in ("DJ", "DS") and st[d.id]) or d.kind == "TR":
            ra, rb = find(d.a), find(d.b)
            if ra != rb:
                parent[ra] = rb
    return find(a) == find(b)


def zone_roots(sub: Sub, st: dict) -> dict:
    """Zonas de proteção: nets ligados por seccionadoras fechadas (disjuntores/TR delimitam)."""
    parent = {n: n for n in sub.nets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in sub.devs.values():
        if d.kind == "DS" and st[d.id]:
            ra, rb = find(d.a), find(d.b)
            if ra != rb:
                parent[ra] = rb
    return {n: find(n) for n in sub.nets}


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------
class Engine:
    DELAY = {"DJ": 0.35, "DS": 2.0, "ST": 2.0}

    def __init__(self, sub: Sub):
        self.sub = sub
        self.soe: list[dict] = []
        self.alarms: list[dict] = []
        self.faults: list[dict] = []
        self.errors = 0
        self.freq = 60.0
        self.pu = 1.0
        self.loadp = {d.id: d.p_nom for d in sub.by_kind("LOAD")}
        self.fault_nets: set[str] = set()
        self.prev_live: dict[str, bool] = {}
        self.demand = 1.0
        self.on_event = None
        self._fid = 0
        self.log("Sistema", "Simulação iniciada – subestação energizada pelas linhas disponíveis.")
        self.refresh()
        self.prev_live = {d.id: d.a in self.res.live for d in sub.by_kind("LOAD")}
        self.prev_live.update({n.id: n.id in self.res.live for n in sub.nets.values() if n.bus})

    # ---- utilidades ------------------------------------------------------
    def now(self):
        return dt.datetime.now()

    def log(self, src, text, dev=None):
        ev = dict(ts=self.now(), src=src, text=text, dev=dev)
        self.soe.append(ev)

    def alarm(self, key, text, sev=2):
        for a in self.alarms:
            if a["key"] == key and a["active"]:
                return
        self.alarms.append(dict(key=key, ts=self.now(), ts_clear=None, text=text,
                                sev=sev, ack=False, active=True))
        self.log("Alarme", text)

    def clear_alarm(self, key):
        for a in self.alarms:
            if a["key"] == key and a["active"]:
                a["active"] = False
                a["ts_clear"] = self.now()

    def ack_all(self):
        for a in self.alarms:
            a["ack"] = True

    def eff_state(self):
        st = self.sub.initial_state()
        for d in self.sub.by_kind(*SWITCH_KINDS):
            st[d.id] = d.closed
        for f in self.faults:
            if f["kind"] == "arc":
                st[f["dev"]] = True
        return st

    def snapshot(self):
        return {d.id: d.closed for d in self.sub.by_kind(*SWITCH_KINDS)}

    def refresh(self):
        self.st = self.eff_state()
        self.res = analyze(self.sub, self.st, self.loadp)
        for n in self.sub.nets.values():
            n.live = n.id in self.res.live

    # ---- comandos --------------------------------------------------------
    def command(self, dev_id, want, who="Operador"):
        d = self.sub.devs[dev_id]
        if d.kind not in SWITCH_KINDS:
            return False, "Equipamento não manobrável."
        if d.tag:
            self.log("Operador", f"Comando em {d.name} BLOQUEADO – equipamento etiquetado ({d.tag}).", d.id)
            return False, f"{d.name} está etiquetado (LOTO): {d.tag}. Remova a etiqueta antes de manobrar."
        if d.damaged:
            return False, f"{d.name} danificado por arco elétrico – aguarde reparo."
        if d.pending:
            return False, f"{d.name} em movimento."
        if d.closed == want and not (d.kind == "DJ" and d.tripped):
            return False, f"{d.name} já está {'fechado' if want else 'aberto'}."
        if d.stuck:
            self.alarm(f"stuck-{d.id}", f"Falha de comando em {d.name} (não respondeu).", 3)
            self.log("Operador", f"Comando {'fechar' if want else 'abrir'} {d.name}: SEM RESPOSTA.", d.id)
            return False, f"{d.name}: falha de comando (mecanismo travado)."
        d.pending = (want, time.time() + self.DELAY[d.kind])
        return True, f"Comando {'FECHAR' if want else 'ABRIR'} enviado para {d.name}."

    def toggle(self, dev_id):
        d = self.sub.devs[dev_id]
        return self.command(dev_id, not d.closed)

    def set_tag(self, dev_id, text="LOTO – Manutenção"):
        d = self.sub.devs[dev_id]
        if d.kind not in SWITCH_KINDS + ("TR",):
            return False, "Este equipamento não aceita etiqueta."
        d.tag = text
        self.log("Operador", f"Etiqueta de bloqueio aplicada em {d.name}: {text}", d.id)
        return True, f"Etiqueta aplicada em {d.name}."

    def set_line(self, dev_id, alive):
        d = self.sub.devs[dev_id]
        d.alive = alive
        d.tries = 0
        self.log("Operador", f"COS: LT {d.name} {'religada' if alive else 'desligada e aterrada no terminal remoto'}.", d.id)
        self.refresh()

    def remove_tag(self, dev_id):
        d = self.sub.devs[dev_id]
        if d.tag:
            self.log("Operador", f"Etiqueta de bloqueio removida de {d.name}.", d.id)
            d.tag = None
        return True, f"Etiqueta removida de {d.name}."

    def _finish(self, d, want):
        sub = self.sub
        self.refresh()
        st, res = self.st, self.res
        verdict = None
        if d.kind == "DS":
            if not want:
                st2 = dict(st)
                st2[d.id] = False
                if not connected(sub, st2, d.a, d.b):
                    s = apparent(res.P.get(d.id, (0, 0)))
                    if (d.a in res.live or d.b in res.live) and s > FLOW_EPS:
                        verdict = f"abertura de seccionadora em carga ({s:.1f} MVA)"
            elif res.root[d.a] != res.root[d.b]:
                st2 = dict(st)
                st2[d.id] = True
                r2 = analyze(sub, st2, self.loadp)
                if r2.short:
                    verdict = "fechamento de seccionadora sobre circuito aterrado"
                elif d.a in res.live and d.b in res.live:
                    verdict = "paralelismo de fontes por seccionadora (sem sincronismo)"
                elif apparent(r2.P.get(d.id, (0, 0))) > FLOW_EPS:
                    verdict = "energização de circuito com carga por seccionadora"
        elif d.kind == "ST" and want:
            st2 = dict(st)
            st2[d.id] = True
            if analyze(sub, st2, self.loadp, solve=False).short:
                verdict = "aplicação de terra em circuito ENERGIZADO"
        elif d.kind == "DJ" and want:
            st2 = dict(st)
            st2[d.id] = True
            if analyze(sub, st2, self.loadp, solve=False).short:
                self.errors += 1
                self.log("Alarme", f"Fechamento de {d.name} sobre barra/circuito aterrado – CURTO-CIRCUITO.", d.id)
        d.closed = want
        d.tripped = False
        self.clear_alarm(f"trip-{d.id}")
        self.log("Operador", f"{KIND_NAMES[d.kind]} {d.name} → {'FECHADO' if want else 'ABERTO'}", d.id)
        if verdict:
            self.errors += 1
            if d.kind == "DS":
                d.damaged = True
                self._add_fault("arc", net=d.a, dev=d.id)
                self.alarm(f"arc-{d.id}", f"ARCO ELÉTRICO em {d.name}: {verdict}.", 3)
            else:
                self.alarm(f"arc-{d.id}", f"ERRO DE MANOBRA em {d.name}: {verdict}.", 3)
            self.log("Alarme", f"ERRO DE MANOBRA – {verdict} em {d.name}.", d.id)
        self.refresh()

    # ---- defeitos --------------------------------------------------------
    def _add_fault(self, kind, net=None, dev=None):
        self._fid += 1
        f = dict(id=self._fid, kind=kind, net=net, dev=dev, since=self.now(), repair_at=None)
        self.faults.append(f)
        return f

    def fault_text(self, f):
        sub = self.sub
        if f["kind"] == "short":
            return f"Curto-circuito em {sub.nets[f['net']].name}"
        if f["kind"] == "arc":
            return f"Arco em {sub.devs[f['dev']].name}"
        if f["kind"] == "trf":
            return f"Falha interna do transformador {sub.devs[f['dev']].name}"
        if f["kind"] == "lineloss":
            return f"Perda de tensão da linha {sub.devs[f['dev']].name}"
        if f["kind"] == "stuck":
            return f"Falha de comando do disjuntor {sub.devs[f['dev']].name}"
        return f"Sobrecarga no alimentador {sub.devs[f['dev']].name}"

    def apply_fault(self, kind, net=None, dev=None):
        sub = self.sub
        if any(f["kind"] == kind and f["net"] == net and f["dev"] == dev for f in self.faults):
            return False, "Defeito já aplicado nesse ponto."
        f = self._add_fault(kind, net, dev)
        d = sub.devs.get(dev) if dev else None
        if kind == "lineloss":
            d.alive = False
        elif kind == "stuck":
            d.stuck = True
        elif kind == "overload":
            d.mult = 1.7
        self.log("Defeito", f"DEFEITO APLICADO: {self.fault_text(f)}.", dev)
        self.alarm(f"fault-{f['id']}", self.fault_text(f), 3)
        self.refresh()
        return True, self.fault_text(f)

    def repair(self, fid):
        f = next((x for x in self.faults if x["id"] == fid), None)
        if not f:
            return False, "Defeito inexistente."
        if f["repair_at"]:
            return False, "Equipe já está trabalhando neste defeito."
        sub = self.sub
        st = self.eff_state()
        zr = zone_roots(sub, st)
        live = self.res.live
        if f["kind"] in ("short", "arc", "trf"):
            nets = [f["net"]] if f["kind"] != "trf" else [sub.devs[f["dev"]].a, sub.devs[f["dev"]].b]
            for n in nets:
                zone = [m for m in sub.nets if zr[m] == zr[n]]
                if any(m in live for m in zone):
                    return False, ("A equipe só pode atuar com a zona DESENERGIZADA e ISOLADA. "
                                   f"Há tensão em {sub.nets[n].name}.")
                if not any(d.kind == "ST" and st[d.id] and zr[d.a] == zr[n] for d in sub.devs.values()):
                    return False, ("Zona sem aterramento de segurança. Feche uma seccionadora de terra "
                                   f"junto a {sub.nets[n].name} antes de liberar a equipe.")
        elif f["kind"] == "stuck":
            d = sub.devs[f["dev"]]
            if d.a in live or d.b in live:
                return False, f"{d.name} ainda está energizado – isole antes de acionar a equipe."
        f["repair_at"] = time.time() + 5
        self.log("Equipe", f"Equipe de campo iniciou reparo: {self.fault_text(f)}.")
        return True, "Equipe de campo atuando (≈5 s)."

    def _finish_repairs(self):
        t = time.time()
        for f in list(self.faults):
            if f["repair_at"] and t >= f["repair_at"]:
                self.faults.remove(f)
                d = self.sub.devs.get(f["dev"]) if f["dev"] else None
                if f["kind"] == "arc":
                    d.damaged = False
                elif f["kind"] == "lineloss":
                    d.alive = True
                elif f["kind"] == "stuck":
                    d.stuck = False
                    self.clear_alarm(f"stuck-{d.id}")
                elif f["kind"] == "overload":
                    d.mult = 1.0
                self.clear_alarm(f"fault-{f['id']}")
                if f["kind"] == "arc":
                    self.clear_alarm(f"arc-{f['dev']}")
                self.log("Equipe", f"Reparo concluído: {self.fault_text(f)}. Equipamento liberado.")
                for l in self.sub.by_kind("LINE"):
                    l.tries = 0
                    if not l.alive and not any(x["kind"] == "lineloss" and x["dev"] == l.id for x in self.faults):
                        l.reclose_at = t + 3

    def apply_lineloss_restore(self):
        pass

    # ---- proteção --------------------------------------------------------
    def _trip(self, d, why):
        d.closed = False
        d.tripped = True
        self.log("Proteção", f"{why}: {KIND_NAMES[d.kind]} {d.name} DESARMOU.", d.id)
        self.alarm(f"trip-{d.id}", f"TRIP {d.name} – {why}", 3)

    def run_protection(self):
        sub = self.sub
        for _ in range(12):
            st = self.eff_state()
            res = analyze(sub, st, solve=False)
            zr = zone_roots(sub, st)
            fz, why = set(), {}
            for f in self.faults:
                if f["kind"] in ("short", "arc"):
                    if f["net"] in res.live:
                        fz.add(zr[f["net"]])
                        why[zr[f["net"]]] = "Diferencial de barra / sobrecorrente (87B/50)"
                elif f["kind"] == "trf":
                    d = sub.devs[f["dev"]]
                    for n in (d.a, d.b):
                        if n in res.live:
                            fz.add(zr[n])
                            why[zr[n]] = "Diferencial do transformador (87T)"
            for d in sub.by_kind("ST"):
                if st[d.id] and d.a in res.live:
                    fz.add(zr[d.a])
                    why[zr[d.a]] = "Curto-circuito fase-terra (terra aplicada em circuito energizado)"
            if not fz:
                return
            acted = False
            tripping = [d for d in sub.by_kind("DJ") if d.closed and zr[d.a] != zr[d.b]
                        and (zr[d.a] in fz or zr[d.b] in fz)]
            for d in tripping:
                z = zr[d.a] if zr[d.a] in fz else zr[d.b]
                if d.stuck:
                    self.log("Proteção", f"FALHA DO DISJUNTOR {d.name}: não abriu – atuação da 50BF.", d.id)
                    self.alarm(f"bf-{d.id}", f"Falha de abertura de {d.name} – 50BF", 3)
                    zs = {zr[d.a], zr[d.b]}
                    for e in sub.by_kind("DJ"):
                        if e is not d and e.closed and zr[e.a] != zr[e.b] and (zr[e.a] in zs or zr[e.b] in zs):
                            self._trip(e, "Proteção de falha de disjuntor (50BF)")
                            acted = True
                else:
                    self._trip(d, why[z])
                    acted = True
            st = self.eff_state()
            res = analyze(sub, st, solve=False)
            for l in sub.by_kind("LINE"):
                if l.alive and zr[l.a] in fz and l.a in res.live:
                    l.alive = False
                    l.tries += 1
                    l.reclose_at = time.time() + 8 if l.tries < 3 else None
                    self.log("Proteção", f"Abertura remota da LT {l.name} (21) – defeito na zona.", l.id)
                    self.alarm(f"lt-{l.id}", f"LT {l.name} desligada pela proteção remota.", 3)
                    acted = True
            if not acted:
                return

    def _reclose_lines(self):
        t = time.time()
        for l in self.sub.by_kind("LINE"):
            if not l.alive and l.reclose_at and t >= l.reclose_at:
                if any(f["kind"] == "lineloss" and f["dev"] == l.id for f in self.faults):
                    continue
                l.alive = True
                l.reclose_at = None
                self.log("Proteção", f"Religamento automático da LT {l.name}.", l.id)
                self.clear_alarm(f"lt-{l.id}")

    # ---- tick ------------------------------------------------------------
    def tick(self):
        t = time.time()
        for d in self.sub.devs.values():
            if d.pending and t >= d.pending[1]:
                want = d.pending[0]
                d.pending = None
                self._finish(d, want)
        self.demand = 1.0
        for d in self.sub.by_kind("LOAD"):
            base = 0.93 + 0.07 * math.sin(t / 50.0 + d.phase)
            self.loadp[d.id] = d.p_nom * d.mult * base * (1 + random.gauss(0, 0.004)) * self.demand
        self.run_protection()
        self._reclose_lines()
        self._finish_repairs()
        self.refresh()
        self.run_protection()
        self._measure(t)
        self._alarms(t)
        self._fault_nets()

    def _fault_nets(self):
        st = self.eff_state()
        zr = zone_roots(self.sub, st)
        zs = set()
        for f in self.faults:
            if f["kind"] in ("short", "arc"):
                zs.add(zr[f["net"]])
            elif f["kind"] == "trf":
                d = self.sub.devs[f["dev"]]
                zs.update((zr[d.a], zr[d.b]))
        self.fault_nets = {n for n in self.sub.nets if zr[n] in zs}

    def _measure(self, t):
        sub, res = self.sub, self.res
        self.pu += random.gauss(0, 0.0006) + (1.0 - self.pu) * 0.02
        self.pu = min(1.03, max(0.97, self.pu))
        self.freq = 60.0 + 0.03 * math.sin(t / 7.0) + random.gauss(0, 0.004)
        trs = sub.by_kind("TR")
        # tensão por raiz de net de MT
        lv_v = {}
        for tr in trs:
            if tr.a in res.live:
                p = apparent(res.P.get(tr.id, (0, 0)))
                v = KV_LV * (1 + 0.0125 * tr.tap) * self.pu - 0.045 * KV_LV * p / tr.mva
                lv_v.setdefault(res.root[tr.b], []).append(v)
        for n in sub.nets.values():
            if n.id not in res.live:
                n.v = 0.0
                continue
            noise = 1 + random.gauss(0, 0.0004)
            if n.kv == KV_HV:
                n.v = KV_HV * self.pu * noise
            else:
                vs = lv_v.get(res.root[n.id])
                n.v = (sum(vs) / len(vs) if vs else KV_LV * self.pu) * noise
        for d in sub.devs.values():
            m = {}
            net = sub.nets[d.a]
            v = net.v
            if d.kind in ("DJ", "DS", "TR"):
                p, q = res.P.get(d.id, (0.0, 0.0)) if self.st.get(d.id, True) or d.kind == "TR" else (0.0, 0.0)
                p, q = abs(p), abs(q)
            elif d.kind == "LINE":
                p, q = res.line_p.get(d.id, (0.0, 0.0))
                p, q = abs(p), abs(q)
            elif d.kind == "LOAD":
                if d.a in res.live:
                    p = self.loadp[d.id]
                    q = p * math.tan(math.acos(d.pf))
                else:
                    p = q = 0.0
            else:
                p = q = 0.0
            noise = 1 + random.gauss(0, 0.003)
            p, q = p * noise, q * noise
            s = math.hypot(p, q)
            m["V"] = v
            m["P"], m["Q"], m["S"] = p, q, s
            m["I"] = s * 1000.0 / (math.sqrt(3) * v) if v > 1 else 0.0
            m["pf"] = p / s if s > 1e-3 else 0.0
            m["load"] = s / d.mva if d.kind == "TR" else 0.0
            d.meas = m

    def _alarms(self, t):
        sub, res = self.sub, self.res
        for tr in sub.by_kind("TR"):
            ld = tr.meas.get("load", 0)
            key = f"ol-{tr.id}"
            if ld > 1.0:
                self.alarm(key, f"Sobrecarga no transformador {tr.name}: {ld * 100:.0f}% da nominal.", 2)
                if ld > 1.3:
                    tr.overload_since = tr.overload_since or t
                    if t - tr.overload_since > 15:
                        zr = zone_roots(sub, self.st)
                        for d in sub.by_kind("DJ"):
                            if d.closed and zr[d.a] != zr[d.b] and (zr[d.a] == zr[tr.b] or zr[d.b] == zr[tr.b]):
                                self._trip(d, "Sobrecarga temporizada (49)")
                        tr.overload_since = None
                else:
                    tr.overload_since = None
            elif ld < 0.95:
                self.clear_alarm(key)
                tr.overload_since = None
        for d in sub.by_kind("LOAD"):
            now = d.a in res.live
            was = self.prev_live.get(d.id, now)
            if was and not now:
                self.alarm(f"lol-{d.id}", f"Perda de alimentação do alimentador {d.name} ({d.p_nom:.1f} MW).", 2)
            if now:
                self.clear_alarm(f"lol-{d.id}")
            self.prev_live[d.id] = now
        for n in sub.nets.values():
            if n.bus:
                now = n.id in res.live
                was = self.prev_live.get(n.id, now)
                if was and not now:
                    self.alarm(f"bus-{n.id}", f"{n.name} SEM TENSÃO.", 1)
                if now:
                    self.clear_alarm(f"bus-{n.id}")
                self.prev_live[n.id] = now

    # ---- métricas / exportação ------------------------------------------
    def lost_load(self):
        return sum(d.p_nom for d in self.sub.by_kind("LOAD") if d.a not in self.res.live)

    def export_soe(self, path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Data/Hora", "Origem", "Evento"])
            for e in self.soe:
                w.writerow([e["ts"].strftime("%d/%m/%Y %H:%M:%S.%f")[:-3], e["src"], e["text"]])

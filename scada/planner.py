"""Planejador genérico de manobras: isolamento para manutenção e retorno à operação."""
from __future__ import annotations

from collections import deque

from .core import FLOW_EPS, KIND_NAMES, SWITCH_KINDS, analyze, apparent, connected

INF = 10 ** 7


# ----------------------------------------------------------------- utilidades
def protected_nets(sub, target):
    kind, ref = target
    if kind == "bus":
        return {ref}
    d = sub.devs[ref]
    if d.kind == "ST":
        return {d.a}
    return {d.a, d.b}


def target_label(sub, target):
    kind, ref = target
    if kind == "bus":
        return sub.nets[ref].name
    return sub.devs[ref].label


def maintenance_targets(sub):
    """Lista (rótulo, target) dos itens que podem ser colocados em manutenção."""
    out = []
    for n in sub.nets.values():
        if n.bus:
            out.append((f"Barramento – {n.name}", ("bus", n.id)))
    for d in sub.devs.values():
        if d.kind in ("DJ", "DS", "TR"):
            out.append((d.label, ("dev", d.id)))
    return out


def _edges(sub, st):
    for d in sub.devs.values():
        if (d.kind in ("DJ", "DS") and st[d.id]) or d.kind == "TR":
            yield d


def _bfs_dist(sub, st, starts, only_closed=True, forbid=()):
    adj = {}
    for d in sub.devs.values():
        if d.kind not in ("DJ", "DS", "TR") or d.id in forbid:
            continue
        if only_closed and d.kind != "TR" and not st[d.id]:
            continue
        adj.setdefault(d.a, []).append((d.b, d))
        adj.setdefault(d.b, []).append((d.a, d))
    dist = {n: 0 for n in starts}
    q = deque(starts)
    while q:
        u = q.popleft()
        for v, _ in adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist, adj


def op_safe(sub, st, d, want, loadp=None):
    if d.kind in ("DJ", "ST"):
        if want:
            return not analyze(sub, {**st, d.id: True}, solve=False).short
        return True
    res = analyze(sub, st, loadp)
    if not want:
        if connected(sub, {**st, d.id: False}, d.a, d.b):
            return True
        live = d.a in res.live or d.b in res.live
        return not (live and apparent(res.P.get(d.id, (0, 0))) > FLOW_EPS)
    if res.root[d.a] == res.root[d.b]:
        return True
    r2 = analyze(sub, {**st, d.id: True}, loadp)
    if r2.short:
        return False
    if d.a in res.live and d.b in res.live:
        return False
    return apparent(r2.P.get(d.id, (0, 0))) <= FLOW_EPS


# -------------------------------------------------------------------- corte mínimo
def min_cut(sub, st, P, forbid_ds=(), maxd=99):
    """Conjunto mínimo de seccionadoras fechadas que isola P das fontes (disjuntores conduzem)."""
    dist, _ = _bfs_dist(sub, st, P)
    cap, devs_on = {}, {}

    def add(u, v, c, d=None):
        cap.setdefault(u, {})
        cap.setdefault(v, {})
        cap[u][v] = cap[u].get(v, 0) + c
        cap[v][u] = cap[v].get(u, 0) + c
        if d is not None:
            devs_on.setdefault(frozenset((u, v)), []).append(d)

    for d in sub.devs.values():
        if d.a == d.b or d.kind not in ("DS", "DJ", "TR"):
            continue
        if d.kind == "DS" and d.id not in forbid_ds:
            dd = min(dist.get(d.a, 30), dist.get(d.b, 30))
            add(d.a, d.b, 100 + dd if dd <= maxd else INF, d)
        else:
            add(d.a, d.b, INF)
    for d in sub.devs.values():
        if d.kind == "LINE" and d.alive:
            add("S", d.a, INF)
    for p in P:
        add(p, "T", INF)
    if "S" not in cap or "T" not in cap:
        return [], 0
    flow = {u: {v: 0 for v in cap[u]} for u in cap}
    total = 0
    while True:
        prev = {"S": None}
        q = deque(["S"])
        while q and "T" not in prev:
            u = q.popleft()
            for v, c in cap[u].items():
                if v not in prev and c - flow[u][v] > 0:
                    prev[v] = u
                    q.append(v)
        if "T" not in prev:
            break
        b, v = INF * 10, "T"
        while prev[v]:
            u = prev[v]
            b = min(b, cap[u][v] - flow[u][v])
            v = u
        v = "T"
        while prev[v]:
            u = prev[v]
            flow[u][v] += b
            flow[v][u] -= b
            v = u
        total += b
        if total >= INF:
            return None, total
    reach = set(prev)
    cut = []
    for u in reach:
        for v, c in cap[u].items():
            if v not in reach and c < INF:
                cut.extend(devs_on.get(frozenset((u, v)), []))
    seen, out = set(), []
    for d in cut:
        if d.id not in seen:
            seen.add(d.id)
            out.append(d)
    return out, total


# ------------------------------------------------------------- restauração de carga
def _restore(sub, s, forbid, P, loads0, loadp):
    s = dict(s)
    added = []
    loads = [d for d in sub.by_kind("LOAD") if d.id in loads0]

    def metric(state):
        r = analyze(sub, state, solve=False)
        if r.short or any(p in r.live for p in P):
            return None
        lost = sum(d.p_nom for d in loads if d.a not in r.live)
        dead = [d for d in loads if d.a not in r.live]
        if not dead:
            return 0.0
        dist, _ = _bfs_dist(sub, state, set(r.live), only_closed=False, forbid=())
        return lost * 1000 + sum(dist.get(d.a, 60) for d in dead)

    cur = metric(s)
    if cur is None:
        return s, added
    for _ in range(25):
        if cur == 0:
            break
        best, bm = None, cur
        for d in sub.by_kind("DJ", "DS"):
            if s[d.id] or d.id in forbid or d.damaged or d.tag:
                continue
            if d.name.endswith("-BP") and any(s[x.id] for x in sub.by_kind("DS") if x.name.endswith("-BP")):
                continue   # só um vão por vez pode estar em by-pass/transferência
            m = metric({**s, d.id: True})
            if m is not None and m < bm - 1e-9:
                best, bm = d, m
        if not best:
            break
        s[best.id] = True
        added.append(best)
        cur = bm
    return s, added


# --------------------------------------------------------------------- sequenciamento
CLS_ISO = {("DS", True): 0, ("DJ", True): 1, ("DJ", False): 2, ("DS", False): 3, ("ST", True): 4, ("ST", False): 0}
CLS_RET = {("ST", False): 0, ("DS", True): 1, ("DJ", True): 2, ("DJ", False): 3, ("DS", False): 4, ("ST", True): 5}


def _unstick(sub, s, d, loadp, taken, want=False):
    """Disjuntor(es) fechado(s) mais próximo(s) cuja abertura torna segura a abertura de d."""
    dist, adj = _bfs_dist(sub, s, {d.a, d.b})
    cands = [sub.devs[xid] for _, xid in sorted((dist.get(x.a, 99), x.id) for x in sub.by_kind("DJ")
                                                if s[x.id] and x.id not in taken)]
    cands = [x for x in cands if not (x.damaged or x.stuck)][:10]
    for x in cands:
        if op_safe(sub, {**s, x.id: False}, d, want, loadp):
            return [x]
    for i, x in enumerate(cands):
        for y in cands[i + 1:]:
            if op_safe(sub, {**s, x.id: False, y.id: False}, d, want, loadp):
                return [x, y]
    return None


def _sequence(sub, st, ops, cls, loadp, allow_unstick=True, reclose=False):
    s = dict(st)
    out, rem, warn = [], list(ops), []
    taken = {d.id for d, _ in ops}
    while rem:
        rem.sort(key=lambda o: (cls.get((o[0].kind, o[1]), 9), o[0].id))
        pick = next((o for o in rem if op_safe(sub, s, o[0], o[1], loadp)), None)
        if pick is None:
            fix = None
            if allow_unstick:
                for o in rem:
                    if o[0].kind == "DS" and (not o[1] or reclose):
                        x = _unstick(sub, s, o[0], loadp, set() if reclose else taken, want=o[1])
                        if x:
                            fix = x
                            break
            if fix:
                for x in fix:
                    taken.add(x.id)
                    out.append((x, False))
                    s[x.id] = False
                    if reclose:
                        rem.append((x, True))
                continue
            pick = rem[0]
            warn.append(f"Não há ordem totalmente segura para {pick[0].name}; operar com cautela.")
            rem.remove(pick)
            s[pick[0].id] = pick[1]
            out.append(pick)
            continue
        rem.remove(pick)
        s[pick[0].id] = pick[1]
        out.append(pick)
    return out, s, warn


def _text(d, want):
    nm = KIND_NAMES[d.kind].lower()
    if d.kind == "ST":
        return f"{'Fechar' if want else 'Abrir'} {nm} {d.name}"
    return f"{'Fechar' if want else 'Abrir'} {nm} {d.name}"


def _lost(sub, st0, st, loadp):
    r0 = analyze(sub, st0, solve=False)
    r = analyze(sub, st, solve=False)
    return [d for d in sub.by_kind("LOAD") if d.a in r0.live and d.a not in r.live]


def plan_isolation(sub, st0, target, loadp=None):
    P = protected_nets(sub, target)
    tdev = sub.devs[target[1]] if target[0] == "dev" else None
    plan = dict(kind="isolation", target=target, label=target_label(sub, target), steps=[], notes=[], lost=[], ok=True)
    r0 = analyze(sub, st0, solve=False)
    loads0 = {d.id for d in sub.by_kind("LOAD") if d.a in r0.live}
    lines_out = [d for d in sub.by_kind("LINE") if d.alive and d.a in P]
    for l in lines_out:
        l.alive = False
    try:
        return _plan_iso_inner(sub, st0, target, loadp, P, tdev, plan, loads0, lines_out)
    finally:
        for l in lines_out:
            l.alive = True


def _plan_iso_inner(sub, st0, target, loadp, P, tdev, plan, loads0, lines_out):
    best = None
    for maxd in (0, 1, 3, 99):
        pl = _plan_iso_once(sub, st0, target, loadp, P, tdev, dict(plan, steps=[], notes=[]), loads0, lines_out, maxd)
        if not pl["ok"]:
            if best is None:
                best = pl
            continue
        key = (len(pl["lost"]), len(pl["notes"]), len(pl["steps"]))
        if best is None or not best["ok"] or key < best["_key"]:
            pl["_key"] = key
            best = pl
        if not pl["lost"] and not pl["notes"]:
            break
    return best


def _plan_iso_once(sub, st0, target, loadp, P, tdev, plan, loads0, lines_out, maxd):
    cut, total = min_cut(sub, st0, P, forbid_ds={tdev.id} if tdev and tdev.kind == "DS" else (), maxd=maxd)
    if cut is None:
        plan.update(ok=False)
        plan["notes"].append("Não é possível isolar este item apenas com seccionadoras (caminho de alimentação "
                             "sem seccionamento visível).")
        return plan
    s1 = dict(st0)
    for d in cut:
        s1[d.id] = False
    forbid = {d.id for d in cut} | {d.id for d in sub.by_kind("ST")}
    if tdev:
        forbid.add(tdev.id)
    s2, added = _restore(sub, s1, forbid, P, loads0, loadp)
    ops = [(d, False) for d in cut if st0[d.id]]
    ops += [(d, True) for d in added]
    if tdev and tdev.kind == "DJ" and st0[tdev.id]:
        ops.append((tdev, False))
    # aterramentos
    sf = dict(s2)
    for d in ops:
        sf[d[0].id] = d[1]
    for p in sorted(P):
        direct = [d for d in sub.by_kind("ST") if d.a == p and d is not tdev]
        if direct:
            if not any(sf[d.id] for d in direct):
                ops.append((direct[0], True))
                sf[direct[0].id] = True
        else:
            plan["notes"].append(f"Sem seccionadora de terra em {sub.nets[p].name}: aterramento temporário (conjunto móvel).")
    seq, sfinal, warn = _sequence(sub, st0, ops, CLS_ISO, loadp)
    plan["notes"] += warn
    rf = analyze(sub, sfinal, solve=False)
    plan["lost"] = [d.name for d in sub.by_kind("LOAD") if d.id in loads0 and d.a not in rf.live]
    tags = []
    if tdev and tdev.kind in SWITCH_KINDS + ("TR",):
        tags.append(tdev)
    tags += [d for d in boundary_ds(sub, sfinal, P, tdev) if d not in tags]
    steps = []
    for l in lines_out:
        steps.append(dict(type="line", dev=l.id, alive=False,
                          text=f"Solicitar ao COS o desligamento e aterramento remoto da LT {l.name}"))
    plan["lines_out"] = [l.id for l in lines_out]
    check_done = False
    for d, want in seq:
        if d.kind == "ST" and want and not check_done:
            names = ", ".join(sorted(sub.nets[p].name for p in P))
            steps.append(dict(type="check", nets=sorted(P), text=f"Confirmar ausência de tensão em: {names}"))
            check_done = True
        steps.append(dict(type="op", dev=d.id, closed=want, text=_text(d, want)))
    if not check_done:
        names = ", ".join(sorted(sub.nets[p].name for p in P))
        steps.append(dict(type="check", nets=sorted(P), text=f"Confirmar ausência de tensão em: {names}"))
    for d in tags:
        steps.append(dict(type="tag", dev=d.id, text=f"Instalar etiqueta de bloqueio (LOTO) em {d.name}"))
    plan["steps"] = steps
    plan["final"] = sfinal
    plan["tags"] = [d.id for d in tags]
    return plan


def plan_return(sub, st_now, st_target, tags, loadp=None, lines_out=()):
    plan = dict(kind="return", label="Retorno à operação", steps=[], notes=[], lost=[], ok=True)
    steps = []
    for did in tags:
        d = sub.devs[did]
        if d.tag:
            steps.append(dict(type="untag", dev=did, text=f"Remover etiqueta de bloqueio de {d.name}"))
    ops = [(sub.devs[k], v) for k, v in st_target.items() if st_now[k] != v]
    seq, sfinal, warn = _sequence(sub, st_now, ops, CLS_RET, loadp, allow_unstick=True, reclose=True)
    plan["notes"] += warn
    for d, want in seq:
        steps.append(dict(type="op", dev=d.id, closed=want, text=_text(d, want)))
    for lid in lines_out:
        steps.append(dict(type="line", dev=lid, alive=True,
                          text=f"Solicitar ao COS o religamento da LT {sub.devs[lid].name}"))
    plan["steps"] = steps
    plan["lost"] = []
    return plan


# ----------------------------------------------------------------- verificação
def boundary_ds(sub, st, P, tdev=None):
    gparent = {n: n for n in sub.nets}

    def gf(x):
        while gparent[x] != x:
            gparent[x] = gparent[gparent[x]]
            x = gparent[x]
        return x

    for d in sub.devs.values():
        if d.kind in ("DJ", "DS") and st[d.id]:
            ra, rb = gf(d.a), gf(d.b)
            if ra != rb:
                gparent[ra] = rb
    pcomp = {gf(p) for p in P}
    return [d for d in sub.by_kind("DS") if not st[d.id] and d is not tdev
            and ((gf(d.a) in pcomp) != (gf(d.b) in pcomp))]


def isolation_report(sub, st, target):
    P = protected_nets(sub, target)
    tdev = sub.devs[target[1]] if target[0] == "dev" else None
    rep = []
    # 1) isolação visível por seccionadoras (disjuntores considerados fechados)
    parent = {n: n for n in sub.nets}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for d in sub.devs.values():
        if (d.kind == "DS" and st[d.id]) or d.kind in ("DJ", "TR"):
            if d.kind == "DJ" and not (st[d.id] or d is tdev):
                pass
            ra, rb = find(d.a), find(d.b)
            if ra != rb:
                parent[ra] = rb
    srcs = {find(d.a) for d in sub.by_kind("LINE") if d.alive}
    for p in sorted(P):
        ok = find(p) not in srcs
        rep.append((ok, f"{sub.nets[p].name}: separado das fontes por seccionadoras abertas (abertura visível)"))
    r = analyze(sub, st, solve=False)
    for p in sorted(P):
        rep.append((p not in r.live, f"{sub.nets[p].name}: sem tensão"))
    # 2) aterramento
    gparent = {n: n for n in sub.nets}

    def gf(x):
        while gparent[x] != x:
            gparent[x] = gparent[gparent[x]]
            x = gparent[x]
        return x

    for d in sub.devs.values():
        if d.kind in ("DJ", "DS") and st[d.id]:
            ra, rb = gf(d.a), gf(d.b)
            if ra != rb:
                gparent[ra] = rb
    earthed = {gf(d.a) for d in sub.by_kind("ST") if st[d.id]}
    for p in sorted(P):
        rep.append((gf(p) in earthed, f"{sub.nets[p].name}: aterrado (seccionadora de terra fechada)"))
    # 3) etiquetas
    if tdev and tdev.kind in SWITCH_KINDS + ("TR",):
        rep.append((bool(tdev.tag), f"{tdev.name}: etiqueta de bloqueio (LOTO) instalada"))
    for d in boundary_ds(sub, st, P, tdev):
        rep.append((bool(d.tag), f"{d.name}: seccionadora de isolação aberta e etiquetada"))
    return rep

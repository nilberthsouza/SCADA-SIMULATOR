"""Interface Qt (PySide6) – estilo ISA-101: fundo cinza, barra viva vermelha, barra morta azul."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDockWidget, QFileDialog,
                               QFrame, QGraphicsObject, QGraphicsPathItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTextEdit, QToolBar, QVBoxLayout, QWidget)

from . import planner
from .core import KIND_NAMES, SWITCH_KINDS, Engine
from .layouts import ARRANGEMENTS, build

BG = QColor("#c3c7cb")
LIVE = QColor("#d7261e")
DEAD = QColor("#1f4fa6")
FAULT = QColor("#ff9500")
INK = QColor("#1b1e22")
OPEN_FILL = QColor("#e9ebed")
MOVING = QColor("#f2d230")
SEL = QColor("#00a0c8")
HILITE = QColor("#00b050")


class Ctx:
    """Estado compartilhado de desenho."""

    def __init__(self, eng):
        self.eng = eng
        self.blink = False
        self.selected = None
        self.hint = set()          # dispositivos destacados pelo modo guiado
        self.window = None

    def netcol(self, nid):
        if nid in self.eng.fault_nets and self.blink:
            return FAULT
        return LIVE if self.eng.sub.nets[nid].live else DEAD


# --------------------------------------------------------------------------- itens
class WireItem(QGraphicsPathItem):
    def __init__(self, ctx, net, pts, bus):
        super().__init__()
        self.ctx, self.net, self.bus = ctx, net, bus
        path = QPainterPath(QPointF(*pts[0]))
        for p in pts[1:]:
            path.lineTo(QPointF(*p))
        self.setPath(path)
        self.setZValue(1 if not bus else 2)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

    def refresh(self):
        w = 7 if self.bus else 3
        pen = QPen(self.ctx.netcol(self.net), w)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        self.setPen(pen)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.ctx.window.net_menu(self.net, ev.screenPos().toPoint())
        else:
            self.ctx.window.net_clicked(self.net)
        ev.accept()


class DotItem(QGraphicsObject):
    def __init__(self, ctx, net, x, y):
        super().__init__()
        self.ctx, self.net = ctx, net
        self.setPos(x, y)
        self.setZValue(3)

    def boundingRect(self):
        return QRectF(-5, -5, 10, 10)

    def paint(self, p, *a):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.ctx.netcol(self.net))
        p.drawEllipse(QPointF(0, 0), 4, 4)


class BusText(QGraphicsObject):
    def __init__(self, ctx, net, x, y, text):
        super().__init__()
        self.ctx, self.net, self.text = ctx, net, text
        self.setPos(x, y)
        self.setZValue(6)

    def boundingRect(self):
        return QRectF(0, -2, 520, 36)

    def paint(self, p, *a):
        n = self.ctx.eng.sub.nets[self.net]
        f = QFont("Segoe UI", 9, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(QPointF(0, 11), self.text)
        if n.live:
            p.setFont(QFont("Consolas", 9))
            p.setPen(LIVE.darker(130))
            p.drawText(QPointF(0, 24), f"{n.v:7.2f} kV   {self.ctx.eng.freq:5.2f} Hz")
        else:
            p.setFont(QFont("Consolas", 9))
            p.setPen(DEAD)
            p.drawText(QPointF(0, 24), "DESENERGIZADA")


class DevItem(QGraphicsObject):
    def __init__(self, ctx, dev, x, y, rot, lab):
        super().__init__()
        self.ctx, self.dev, self.lab = ctx, dev, lab
        self.setPos(x, y)
        self.setRotation(rot)
        self.rot = rot
        self.setZValue(5)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor if dev.kind in SWITCH_KINDS else Qt.CursorShape.ArrowCursor)

    def boundingRect(self):
        return QRectF(-90, -90, 180, 180)

    def shape(self):
        pth = QPainterPath()
        pth.addRect(QRectF(-14, -20, 28, 40))
        return pth

    # ---- interação
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.RightButton:
            self.ctx.window.dev_menu(self.dev, ev.screenPos().toPoint())
        else:
            self.ctx.window.dev_clicked(self.dev)
        ev.accept()

    def mouseDoubleClickEvent(self, ev):
        if self.dev.kind in SWITCH_KINDS:
            self.ctx.window.operate(self.dev)
        ev.accept()

    def hoverEnterEvent(self, ev):
        d = self.dev
        m = d.meas
        tip = f"{d.label}"
        if d.kind in SWITCH_KINDS:
            tip += f"\nEstado: {'FECHADO' if d.closed else 'ABERTO'}"
        if d.tag:
            tip += f"\nETIQUETA: {d.tag}"
        if m and d.kind != "ST" and m.get("V", 0) > 1:
            tip += f"\nV={m['V']:.2f} kV  I={m['I']:.0f} A\nP={m['P']:.2f} MW  Q={m['Q']:.2f} Mvar  FP={m['pf']:.2f}"
        self.setToolTip(tip)

    # ---- desenho
    def _label(self, p, d):
        p.save()
        p.rotate(-self.rot)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        txt = d.name
        meas = None
        m = d.meas
        live = self.ctx.eng.sub.nets[d.a].live
        if live and d.kind in ("DJ", "LINE", "TR", "LOAD") and m:
            if d.kind == "DJ" and d.closed or d.kind != "DJ":
                meas = f"{m['I']:.0f} A  {m['P']:.1f} MW"
                meas2 = f"{m['Q']:.1f} Mvar  FP {m['pf']:.2f}"
                if d.kind == "TR":
                    meas2 = f"{m['load'] * 100:.0f}% carga  FP {m['pf']:.2f}"
        w = 120
        x, y, al = 0, 0, Qt.AlignmentFlag.AlignHCenter
        if self.lab == "L":
            r = QRectF(-w - 14, -16, w, 32)
            al = Qt.AlignmentFlag.AlignRight
        elif self.lab == "R":
            r = QRectF(14, -16, w, 32)
            al = Qt.AlignmentFlag.AlignLeft
        elif self.lab == "U":
            r = QRectF(-w / 2, -48, w, 28)
            al = Qt.AlignmentFlag.AlignHCenter
        else:
            r = QRectF(-w / 2, 22, w, 40)
            al = Qt.AlignmentFlag.AlignHCenter
        col = INK
        if d.tag:
            txt = "⛔ " + txt
            col = QColor("#8a0b8a")
        p.setPen(col)
        p.drawText(QRectF(r.x(), r.y(), r.width(), 14), al | Qt.AlignmentFlag.AlignVCenter, txt)
        if meas:
            p.setFont(QFont("Consolas", 7))
            p.setPen(QColor("#222"))
            p.drawText(QRectF(r.x(), r.y() + 12, r.width(), 11), al | Qt.AlignmentFlag.AlignVCenter, meas)
            p.drawText(QRectF(r.x(), r.y() + 22, r.width(), 11), al | Qt.AlignmentFlag.AlignVCenter, meas2)
        p.restore()

    def paint(self, p, *args):
        d, c = self.dev, self.ctx
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = d.kind
        ca = c.netcol(d.a)
        cb = c.netcol(d.b) if d.b else None
        if k == "DJ":
            self._breaker(p, d, ca, cb)
        elif k == "DS":
            self._disc(p, d, ca, cb)
        elif k == "ST":
            self._earth(p, d, ca)
        elif k == "TR":
            self._trafo(p, d, ca, cb)
        elif k == "LINE":
            self._line(p, d, ca)
        elif k == "LOAD":
            self._load(p, d, ca)
        if d.id in c.hint:
            p.setPen(QPen(HILITE, 3, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(-18, -24, 36, 48), 5, 5)
        if c.selected == d.id:
            p.setPen(QPen(SEL, 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(-16, -22, 32, 44), 4, 4)
        self._label(p, d)

    def _breaker(self, p, d, ca, cb):
        p.setPen(QPen(ca, 3))
        p.drawLine(0, -18, 0, -9)
        p.setPen(QPen(cb, 3))
        p.drawLine(0, 9, 0, 18)
        moving = d.pending is not None
        if moving:
            fill = MOVING
        elif d.closed:
            fill = ca
        else:
            fill = OPEN_FILL
        pen = QPen(INK, 2)
        if d.tripped:
            pen = QPen(FAULT if self.ctx.blink else INK, 3)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRect(QRectF(-9, -9, 18, 18))
        if d.damaged or d.stuck:
            p.setPen(QPen(INK, 1))
            p.drawLine(-9, -9, 9, 9)
            p.drawLine(-9, 9, 9, -9)

    def _disc(self, p, d, ca, cb):
        moving = d.pending is not None
        p.setPen(QPen(ca, 3))
        p.drawLine(0, -18, 0, -8)
        p.setPen(QPen(cb, 3))
        p.drawLine(0, 8, 0, 18)
        p.setPen(QPen(INK, 1))
        p.setBrush(INK)
        p.drawEllipse(QPointF(0, -8), 2.2, 2.2)
        p.drawEllipse(QPointF(0, 8), 2.2, 2.2)
        closed = d.closed
        if moving:
            p.setPen(QPen(MOVING, 3, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(0, 8), QPointF(7, -6))
        elif closed:
            p.setPen(QPen(INK, 1.5))
            p.setBrush(ca)
            p.drawRect(QRectF(-3.5, -8, 7, 16))
        else:
            p.setPen(QPen(INK, 3))
            p.drawLine(QPointF(0, 8), QPointF(9, -5))
        if d.damaged:
            p.setPen(QPen(FAULT, 3))
            p.drawLine(-8, -8, 8, 8)
            p.drawLine(-8, 8, 8, -8)

    def _earth(self, p, d, ca):
        p.setPen(QPen(ca, 3))
        p.drawLine(0, -18, 0, -8)
        p.setPen(QPen(INK, 1))
        p.setBrush(INK)
        p.drawEllipse(QPointF(0, -8), 2.2, 2.2)
        gcol = QColor("#2d7d2d") if d.closed else QColor("#555")
        if d.pending is not None:
            gcol = MOVING
        if d.closed:
            p.setPen(QPen(ca, 3))
            p.drawLine(0, -8, 0, 4)
        else:
            p.setPen(QPen(INK, 2.5))
            p.drawLine(QPointF(0, 2), QPointF(7, -6))
        p.setPen(QPen(gcol, 2))
        p.drawLine(0, 4, 0, 8) if d.closed else None
        for i, w in enumerate((10, 6, 2)):
            y = 8 + i * 4
            p.drawLine(-w, y, w, y)

    def _trafo(self, p, d, ca, cb):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ca, 3))
        p.drawLine(0, -18, 0, -13)
        p.drawEllipse(QPointF(0, -5), 10, 10)
        p.setPen(QPen(cb, 3))
        p.drawEllipse(QPointF(0, 5), 10, 10)
        p.drawLine(0, 15, 0, 18)
        if d.tag:
            p.setPen(QPen(QColor("#8a0b8a"), 2))
            p.drawLine(-14, -14, 14, 14)

    def _line(self, p, d, ca):
        col = ca if d.alive else QColor("#777")
        p.setPen(QPen(col, 3))
        p.drawLine(0, -18, 0, 4)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([QPointF(-8, 4), QPointF(8, 4), QPointF(0, 16)]))
        if not d.alive:
            p.setPen(QPen(FAULT, 3))
            p.drawLine(-9, -2, 9, 12)
            p.drawLine(-9, 12, 9, -2)

    def _load(self, p, d, ca):
        p.setPen(QPen(ca, 3))
        p.drawLine(0, -18, 0, 2)
        p.setBrush(ca)
        p.drawPolygon(QPolygonF([QPointF(-8, 2), QPointF(8, 2), QPointF(0, 14)]))


# ------------------------------------------------------------------------ vistas
class View(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(BG)
        self.setFrameShape(QFrame.Shape.NoFrame)

    def wheelEvent(self, ev):
        f = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)

    def fit(self):
        r = self.scene().itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self.setSceneRect(r)
        self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)


# ------------------------------------------------------------------- janela
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Simulador SCADA – Subestação (manobras)")
        self.resize(1500, 900)
        self.eng = None
        self.ctx = None
        self.armed = None            # defeito aguardando clique
        self.session = None          # sessão de manutenção guiada
        self._build_ui()
        self.new_substation("single", 2, 2)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.fast = QTimer(self)
        self.fast.timeout.connect(self.refresh_items)
        self.fast.start(100)

    # ---- construção da UI
    def _build_ui(self):
        tb = QToolBar("Configuração")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel("  Arranjo de barra: "))
        self.cb_arr = QComboBox()
        for k, v in ARRANGEMENTS.items():
            self.cb_arr.addItem(v, k)
        tb.addWidget(self.cb_arr)
        tb.addWidget(QLabel("  Transformadores: "))
        self.sp_nt = QSpinBox()
        self.sp_nt.setRange(1, 4)
        self.sp_nt.setValue(2)
        tb.addWidget(self.sp_nt)
        tb.addWidget(QLabel("  Linhas: "))
        self.sp_nl = QSpinBox()
        self.sp_nl.setRange(1, 3)
        self.sp_nl.setValue(2)
        tb.addWidget(self.sp_nl)
        b = QPushButton("Criar / reiniciar subestação")
        b.clicked.connect(self.apply_config)
        tb.addWidget(b)
        tb.addSeparator()
        bm = QPushButton("🛠 Simular manutenção…")
        bm.clicked.connect(self.open_maintenance)
        tb.addWidget(bm)
        self.bf = QPushButton("⚡ Simular defeito ▾")
        mf = QMenu(self)
        for txt, kind in (("Curto-circuito em barra/zona (clique no diagrama)", "short"),
                          ("Falha interna de transformador (clique no TR)", "trf"),
                          ("Perda de tensão de linha (clique na LT)", "lineloss"),
                          ("Disjuntor travado – não opera (clique no DJ)", "stuck"),
                          ("Sobrecarga de alimentador (clique na carga)", "overload")):
            mf.addAction(txt, lambda k=kind: self.arm_fault(k))
        self.bf.setMenu(mf)
        tb.addWidget(self.bf)
        tb.addSeparator()
        ba = QPushButton("Reconhecer alarmes")
        ba.clicked.connect(lambda: (self.eng.ack_all(), self.fill_alarms()))
        tb.addWidget(ba)
        be = QPushButton("Exportar SOE (CSV)")
        be.clicked.connect(self.export_soe)
        tb.addWidget(be)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        # painel direito
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        self.info.setMinimumWidth(310)
        self.btn_op = QPushButton("Abrir / Fechar")
        self.btn_op.clicked.connect(lambda: self.selected_dev() and self.operate(self.selected_dev()))
        self.btn_tag = QPushButton("Etiqueta (LOTO)")
        self.btn_tag.clicked.connect(self.toggle_tag)
        pw = QWidget()
        lay = QVBoxLayout(pw)
        lay.addWidget(QLabel("<b>Equipamento selecionado</b>"))
        lay.addWidget(self.info, 1)
        row = QHBoxLayout()
        row.addWidget(self.btn_op)
        row.addWidget(self.btn_tag)
        lay.addLayout(row)
        self.lbl_desc = QLabel()
        self.lbl_desc.setWordWrap(True)
        self.lbl_desc.setStyleSheet("color:#333")
        lay.addWidget(QLabel("<b>Sobre o arranjo</b>"))
        lay.addWidget(self.lbl_desc)
        self.dock_r = QDockWidget("Operação", self)
        self.dock_r.setWidget(pw)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_r)
        # guiado
        gw = QWidget()
        gl = QVBoxLayout(gw)
        self.lbl_g = QLabel("Nenhuma manutenção em andamento.\nUse o botão “Simular manutenção…”.")
        self.lbl_g.setWordWrap(True)
        gl.addWidget(self.lbl_g)
        self.lst_g = QListWidget()
        self.lst_g.itemDoubleClicked.connect(lambda it: self.goto_step(self.lst_g.row(it)))
        gl.addWidget(self.lst_g, 1)
        r2 = QHBoxLayout()
        self.chk_hint = QPushButton("Executar próximo passo")
        self.chk_hint.clicked.connect(self.exec_next)
        self.btn_verify = QPushButton("Verificar isolamento")
        self.btn_verify.clicked.connect(self.verify)
        r2.addWidget(self.chk_hint)
        r2.addWidget(self.btn_verify)
        gl.addLayout(r2)
        r3 = QHBoxLayout()
        self.btn_return = QPushButton("Concluir manutenção → retorno à operação")
        self.btn_return.clicked.connect(self.start_return)
        self.btn_end = QPushButton("Encerrar sessão")
        self.btn_end.clicked.connect(self.end_session)
        r3.addWidget(self.btn_return)
        r3.addWidget(self.btn_end)
        gl.addLayout(r3)
        self.dock_g = QDockWidget("Manutenção guiada", self)
        self.dock_g.setWidget(gw)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_g)
        self.tabifyDockWidget(self.dock_r, self.dock_g)
        self.dock_r.raise_()
        # inferior
        self.tb_alarm = self._table(["Hora", "Sev", "Mensagem", "Estado", "Rec."])
        self.tb_soe = self._table(["Data / hora (ms)", "Origem", "Evento"])
        self.tb_fault = self._table(["Defeito", "Desde", "Situação"])
        self.btn_rep = QPushButton("Acionar equipe de campo para reparar o defeito selecionado")
        self.btn_rep.clicked.connect(self.repair_selected)
        fw = QWidget()
        fl = QVBoxLayout(fw)
        fl.addWidget(self.tb_fault)
        fl.addWidget(self.btn_rep)
        self.bottom = QTabWidget()
        self.bottom.addTab(self.tb_alarm, "Alarmes")
        self.bottom.addTab(self.tb_soe, "SOE – Sequência de eventos")
        self.bottom.addTab(fw, "Defeitos ativos")
        self.dock_b = QDockWidget("Eventos", self)
        self.dock_b.setWidget(self.bottom)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock_b)
        self.lbl_status = QLabel()
        self.statusBar().addPermanentWidget(self.lbl_status)
        self.resizeDocks([self.dock_b], [210], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_r], [380], Qt.Orientation.Horizontal)

    def _table(self, cols):
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.setFont(QFont("Consolas", 9))
        return t

    # ---- subestação
    def apply_config(self):
        self.new_substation(self.cb_arr.currentData(), self.sp_nt.value(), self.sp_nl.value())

    def new_substation(self, arr, nt, nl):
        self.end_session(silent=True)
        sub = build(arr, nt, nl)
        self.eng = Engine(sub)
        self.eng.tick()
        self.ctx = Ctx(self.eng)
        self.ctx.window = self
        self.armed = None
        self.scenes = {}
        self.items = []
        self.dev_items = {}
        self.tabs.clear()
        for key, title in (("HV", "Alta tensão – 138 kV"), ("LV", "Média tensão – 13,8 kV")):
            sc = QGraphicsScene(self)
            sc.setBackgroundBrush(BG)
            self.scenes[key] = sc
            v = View(sc)
            setattr(self, f"view_{key}", v)
            self.tabs.addTab(v, title)
        for scene, net, pts, bus in sub.wires:
            w = WireItem(self.ctx, net, pts, bus)
            self.scenes[scene].addItem(w)
            self.items.append(w)
        for scene, net, x, y in sub.dots:
            it = DotItem(self.ctx, net, x, y)
            self.scenes[scene].addItem(it)
            self.items.append(it)
        for scene, net, x, y, text in sub.texts:
            it = BusText(self.ctx, net, x, y, text)
            self.scenes[scene].addItem(it)
            self.items.append(it)
        for d, scene, x, y, rot, lab in sub.places:
            it = DevItem(self.ctx, d, x, y, rot, lab)
            self.scenes[scene].addItem(it)
            self.items.append(it)
            self.dev_items.setdefault(d.id, []).append((scene, it))
        self.setWindowTitle(f"Simulador SCADA – {sub.title}")
        self.lbl_desc.setText(sub.description)
        self.cb_arr.setCurrentIndex(self.cb_arr.findData(arr))
        self.sp_nt.setValue(nt)
        self.sp_nl.setValue(nl)
        QTimer.singleShot(50, lambda: (self.view_HV.fit(), self.view_LV.fit()))
        self.fill_all()

    # ---- ciclo
    def tick(self):
        self.eng.tick()
        self.ctx.blink = not self.ctx.blink
        self.fill_all()
        self.session_update()

    def refresh_items(self):
        for it in self.items:
            if isinstance(it, WireItem):
                it.refresh()
            else:
                it.update()

    def fill_all(self):
        self.fill_alarms()
        self.fill_soe()
        self.fill_faults()
        self.fill_info()
        self.refresh_items()
        e = self.eng
        act = sum(1 for a in e.alarms if a["active"] and not a["ack"])
        self.lbl_status.setText(
            f"  {e.now().strftime('%d/%m/%Y %H:%M:%S')}   |   f = {e.freq:.2f} Hz   |   "
            f"Alarmes não reconhecidos: {act}   |   Carga interrompida: {e.lost_load():.1f} MW   |   "
            f"Erros de manobra: {e.errors}  ")
        self.lbl_status.setStyleSheet("color:#b00000;font-weight:bold" if e.errors or e.lost_load() > 0 else "")

    def fill_alarms(self):
        t = self.tb_alarm
        al = sorted(self.eng.alarms, key=lambda a: a["ts"], reverse=True)
        t.setRowCount(len(al))
        for i, a in enumerate(al):
            vals = [a["ts"].strftime("%H:%M:%S"), "●" * a["sev"], a["text"],
                    "ATIVO" if a["active"] else "normalizado", "sim" if a["ack"] else "NÃO"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if a["active"] and not a["ack"]:
                    it.setBackground(QColor("#ffb3ad") if a["sev"] >= 3 else QColor("#ffe3a3"))
                elif not a["active"]:
                    it.setForeground(QColor("#555"))
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)

    def fill_soe(self):
        t = self.tb_soe
        ev = self.eng.soe
        if t.rowCount() == len(ev):
            return
        t.setRowCount(len(ev))
        for i in range(t.rowCount()):
            e = ev[len(ev) - 1 - i]
            vals = [e["ts"].strftime("%d/%m/%Y %H:%M:%S.%f")[:-3], e["src"], e["text"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if e["src"] in ("Proteção", "Defeito", "Alarme"):
                    it.setForeground(QColor("#b00000"))
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)

    def fill_faults(self):
        t = self.tb_fault
        fs = self.eng.faults
        sel = t.currentRow()
        t.setRowCount(len(fs))
        for i, f in enumerate(fs):
            vals = [self.eng.fault_text(f), f["since"].strftime("%H:%M:%S"),
                    "Reparo em curso…" if f["repair_at"] else "Aguardando isolamento/reparo"]
            for j, v in enumerate(vals):
                t.setItem(i, j, QTableWidgetItem(v))
        if 0 <= sel < len(fs):
            t.selectRow(sel)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)

    # ---- seleção / info
    def selected_dev(self):
        return self.eng.sub.devs.get(self.ctx.selected) if self.ctx.selected else None

    def fill_info(self):
        d = self.selected_dev()
        if not d:
            self.info.setHtml("<i>Clique em um equipamento no diagrama.<br>Duplo clique em disjuntor/seccionadora "
                              "executa a manobra. Clique direito: mais opções.</i>")
            self.btn_op.setEnabled(False)
            self.btn_tag.setEnabled(False)
            return
        m = d.meas
        h = f"<h3>{d.label}</h3>"
        if d.kind in SWITCH_KINDS:
            st = "FECHADO" if d.closed else "ABERTO"
            if d.pending:
                st = "EM MOVIMENTO…"
            h += f"<p>Estado: <b>{st}</b></p>"
            if d.tripped:
                h += "<p style='color:#c00'><b>DESARMADO por proteção</b></p>"
            if d.damaged:
                h += "<p style='color:#c00'><b>DANIFICADO (arco elétrico)</b></p>"
            if d.stuck:
                h += "<p style='color:#c00'><b>Mecanismo travado</b></p>"
        if d.kind == "LINE":
            h += f"<p>Tensão remota: <b>{'presente' if d.alive else 'AUSENTE'}</b></p>"
        if d.tag:
            h += f"<p style='color:#8a0b8a'><b>⛔ Etiqueta de bloqueio:</b> {d.tag}</p>"
        net = self.eng.sub.nets[d.a]
        h += f"<p>Terminal: {net.name} – {'<b style=color:#d7261e>ENERGIZADO</b>' if net.live else '<b style=color:#1f4fa6>desenergizado</b>'}</p>"
        if d.kind == "ST" and False:
            pass
        if m and d.kind != "ST" and net.live:
            h += (f"<table cellspacing=3><tr><td>Tensão</td><td><b>{m['V']:.2f} kV</b></td></tr>"
                  f"<tr><td>Corrente</td><td><b>{m['I']:.0f} A</b></td></tr>"
                  f"<tr><td>Potência ativa</td><td><b>{m['P']:.2f} MW</b></td></tr>"
                  f"<tr><td>Potência reativa</td><td><b>{m['Q']:.2f} Mvar</b></td></tr>"
                  f"<tr><td>Aparente</td><td><b>{m['S']:.2f} MVA</b></td></tr>"
                  f"<tr><td>Fator de potência</td><td><b>{m['pf']:.3f}</b></td></tr>")
            if d.kind == "TR":
                h += f"<tr><td>Carregamento</td><td><b>{m['load'] * 100:.0f}%</b> de {d.mva:.0f} MVA</td></tr>"
            h += f"<tr><td>Frequência</td><td><b>{self.eng.freq:.2f} Hz</b></td></tr></table>"
        self.info.setHtml(h)
        self.btn_op.setEnabled(d.kind in SWITCH_KINDS)
        if d.kind in SWITCH_KINDS:
            self.btn_op.setText("Abrir" if d.closed else "Fechar")
        self.btn_tag.setEnabled(d.kind in SWITCH_KINDS + ("TR",))
        self.btn_tag.setText("Remover etiqueta" if d.tag else "Aplicar etiqueta (LOTO)")

    def dev_clicked(self, d):
        if self.armed:
            self.apply_armed(dev=d)
            return
        self.ctx.selected = d.id
        self.fill_info()

    def net_clicked(self, nid):
        if self.armed:
            self.apply_armed(net=nid)

    def operate(self, d):
        if d.kind not in SWITCH_KINDS:
            return
        ok, msg = self.eng.toggle(d.id)
        self.statusBar().showMessage(msg, 5000)
        if not ok:
            QMessageBox.warning(self, "Manobra não executada", msg)
        self.ctx.selected = d.id
        self.fill_all()

    def toggle_tag(self):
        d = self.selected_dev()
        if not d:
            return
        if d.tag:
            self.eng.remove_tag(d.id)
        else:
            self.eng.set_tag(d.id, "LOTO – Manutenção")
        self.fill_all()

    def dev_menu(self, d, pos):
        m = QMenu(self)
        if d.kind in SWITCH_KINDS:
            m.addAction("Abrir" if d.closed else "Fechar", lambda: self.operate(d))
        if d.kind in SWITCH_KINDS + ("TR",):
            if d.tag:
                m.addAction("Remover etiqueta (LOTO)", lambda: (self.eng.remove_tag(d.id), self.fill_all()))
            else:
                m.addAction("Aplicar etiqueta (LOTO)", lambda: (self.eng.set_tag(d.id), self.fill_all()))
        if d.kind in ("DJ", "DS", "TR"):
            m.addAction("Colocar em manutenção (guiado)…", lambda: self.open_maintenance(("dev", d.id)))
        m.addSeparator()
        f = {"TR": ("Defeito interno no transformador", "trf"), "LINE": ("Perda de tensão da linha", "lineloss"),
             "DJ": ("Disjuntor travado", "stuck"), "LOAD": ("Sobrecarga do alimentador", "overload")}.get(d.kind)
        if f:
            m.addAction(f[0], lambda: self.apply_fault(f[1], dev=d.id))
        m.addAction("Curto-circuito neste ponto", lambda: self.apply_fault("short", net=d.a))
        m.exec(pos)

    def net_menu(self, nid, pos):
        m = QMenu(self)
        n = self.eng.sub.nets[nid]
        m.addAction(f"Curto-circuito em {n.name}", lambda: self.apply_fault("short", net=nid))
        if n.bus:
            m.addAction("Colocar barramento em manutenção (guiado)…", lambda: self.open_maintenance(("bus", nid)))
        m.exec(pos)

    # ---- defeitos
    def arm_fault(self, kind):
        self.armed = kind
        self.statusBar().showMessage("Clique no equipamento/barra onde o defeito deve ocorrer (Esc cancela).", 20000)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape and self.armed:
            self.armed = None
            self.unsetCursor()
            self.statusBar().clearMessage()
        super().keyPressEvent(ev)

    def apply_armed(self, dev=None, net=None):
        kind, self.armed = self.armed, None
        self.unsetCursor()
        if kind == "short":
            self.apply_fault("short", net=net or dev.a)
        else:
            want = {"trf": "TR", "lineloss": "LINE", "stuck": "DJ", "overload": "LOAD"}[kind]
            if not dev or dev.kind != want:
                QMessageBox.information(self, "Defeito", f"Este defeito exige clicar em um(a) {KIND_NAMES[want]}.")
                return
            self.apply_fault(kind, dev=dev.id)

    def apply_fault(self, kind, net=None, dev=None):
        ok, msg = self.eng.apply_fault(kind, net=net, dev=dev)
        self.statusBar().showMessage(msg, 6000)
        self.eng.tick()
        self.fill_all()

    def repair_selected(self):
        row = self.tb_fault.currentRow()
        if row < 0 or row >= len(self.eng.faults):
            QMessageBox.information(self, "Reparo", "Selecione um defeito na lista.")
            return
        ok, msg = self.eng.repair(self.eng.faults[row]["id"])
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Equipe de campo", msg)

    def export_soe(self):
        p, _ = QFileDialog.getSaveFileName(self, "Exportar SOE", "SOE.csv", "CSV (*.csv)")
        if p:
            self.eng.export_soe(p)

    # ---------------------------------------------------------------- manutenção guiada
    def open_maintenance(self, target=None):
        if not isinstance(target, tuple):
            target = None
        if target is None:
            items = planner.maintenance_targets(self.eng.sub)
            sel = self.selected_dev()
            from PySide6.QtWidgets import QInputDialog
            labels = [t[0] for t in items]
            start = 0
            if sel:
                for i, (lab, tg) in enumerate(items):
                    if tg == ("dev", sel.id):
                        start = i
            lab, ok = QInputDialog.getItem(self, "Simular manutenção",
                                           "Equipamento a ser liberado para manutenção:", labels, start, False)
            if not ok:
                return
            target = items[labels.index(lab)][1]
        mode = QMessageBox(self)
        mode.setWindowTitle("Modo de manutenção")
        mode.setText(f"Manutenção em: {planner.target_label(self.eng.sub, target)}\n\n"
                     "Guiado: o sistema calcula e acompanha o procedimento de manobra.\n"
                     "Livre: você faz as manobras e depois pede a verificação do isolamento.")
        bg = mode.addButton("Guiado", QMessageBox.ButtonRole.AcceptRole)
        bl = mode.addButton("Livre", QMessageBox.ButtonRole.ActionRole)
        mode.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        mode.exec()
        if mode.clickedButton() not in (bg, bl):
            return
        self.end_session(silent=True)
        eng = self.eng
        self.session = dict(target=target, st0=eng.snapshot(), phase="isolation", tags=[], steps=[], idx=0,
                            guided=(mode.clickedButton() is bg), done=False)
        if self.session["guided"]:
            self.make_plan()
        else:
            self.lbl_g.setText(f"<b>Manutenção livre:</b> {planner.target_label(eng.sub, target)}<br>"
                               "Realize as manobras, aterramentos e etiquetas; depois use “Verificar isolamento”.")
            self.lst_g.clear()
        self.dock_g.raise_()
        self.session_update()

    def make_plan(self):
        s = self.session
        eng = self.eng
        if s["phase"] == "isolation":
            plan = planner.plan_isolation(eng.sub, eng.snapshot(), s["target"], eng.loadp)
            s["tags"] = plan.get("tags", s["tags"])
            s["lines_out"] = plan.get("lines_out", [])
        else:
            plan = planner.plan_return(eng.sub, eng.snapshot(), s["st0"], s["tags"], eng.loadp,
                                       s.get("lines_out", []))
        s["plan"] = plan
        s["plan_state"] = eng.snapshot()
        s["steps"] = plan["steps"]
        s["marks"] = [False] * len(plan["steps"])
        head = f"<b>{planner.target_label(eng.sub, s['target'])}</b><br>" if s["phase"] == "isolation" else "<b>Retorno à operação</b><br>"
        if not plan["ok"]:
            head += "<span style='color:#b00'>Planejamento impossível.</span><br>"
        if plan["lost"]:
            head += f"<span style='color:#b00'>Cargas interrompidas por este procedimento: {', '.join(plan['lost'])}.</span><br>"
        elif plan["ok"] and s["phase"] == "isolation":
            head += "<span style='color:#060'>Sem interrupção de carga.</span><br>"
        for n in plan["notes"]:
            head += f"<i>{n}</i><br>"
        self.lbl_g.setText(head)
        self.render_steps()

    def render_steps(self):
        s = self.session
        self.lst_g.clear()
        for i, st in enumerate(s["steps"]):
            it = QListWidgetItem(f"{i + 1}. {st['text']}")
            self.lst_g.addItem(it)
        self.paint_steps()

    def paint_steps(self):
        s = self.session
        if not s or not s["guided"]:
            return
        first = True
        self.ctx.hint = set()
        for i, st in enumerate(s["steps"]):
            it = self.lst_g.item(i)
            if it is None:
                continue
            if s["marks"][i]:
                it.setBackground(QColor("#c8e6c9"))
                it.setText(f"✔ {i + 1}. {st['text']}")
            elif first:
                it.setBackground(QColor("#fff2a8"))
                it.setText(f"▶ {i + 1}. {st['text']}")
                first = False
                s["idx"] = i
                if st.get("dev"):
                    self.ctx.hint = {st["dev"]}
            else:
                it.setBackground(QColor("#ffffff"))
                it.setText(f"{i + 1}. {st['text']}")
        s["all_done"] = first

    def goto_step(self, row):
        if not self.session or row < 0 or row >= len(self.session["steps"]):
            return
        st = self.session["steps"][row]
        if st.get("dev"):
            self.focus_device(st["dev"])

    def focus_device(self, did):
        for scene, it in self.dev_items.get(did, [])[:1]:
            self.tabs.setCurrentIndex(0 if scene == "HV" else 1)
            v = self.view_HV if scene == "HV" else self.view_LV
            v.centerOn(it)

    def step_done(self, st):
        eng = self.eng
        if st["type"] == "op":
            d = eng.sub.devs[st["dev"]]
            return d.closed == st["closed"] and d.pending is None
        if st["type"] == "line":
            return eng.sub.devs[st["dev"]].alive == st["alive"]
        if st["type"] == "tag":
            return bool(eng.sub.devs[st["dev"]].tag)
        if st["type"] == "untag":
            return not eng.sub.devs[st["dev"]].tag
        if st["type"] == "check":
            return all(not eng.sub.nets[n].live for n in st["nets"])
        return False

    def session_update(self):
        s = self.session
        if not s or not s["guided"] or "plan" not in s:
            return
        eng = self.eng
        # marca passos concluídos (em ordem)
        progressed = False
        for i, st in enumerate(s["steps"]):
            if not s["marks"][i]:
                if self.step_done(st) and all(s["marks"][:i]):
                    s["marks"][i] = True
                    progressed = True
                elif st["type"] == "op" and all(s["marks"][:i]):
                    # usuário fez algo diferente do planejado? replaneja se o estado divergiu
                    pass
                break
        # divergência: algum equipamento de passo futuro/anterior mudou de forma inesperada
        if self.deviated():
            self.statusBar().showMessage("Manobra fora do plano – procedimento recalculado.", 6000)
            self.make_plan()
        self.paint_steps()
        if s.get("all_done") and not s["done"] and s["steps"]:
            s["done"] = True
            if s["phase"] == "isolation":
                QMessageBox.information(self, "Equipamento liberado",
                                        f"{planner.target_label(eng.sub, s['target'])} está isolado, aterrado e etiquetado.\n"
                                        "Quando quiser, use “Concluir manutenção → retorno à operação”.")
            else:
                QMessageBox.information(self, "Normalizado",
                                        "Sistema retornou à configuração original. Manutenção concluída.")
                self.end_session(silent=True)

    def deviated(self):
        s = self.session
        eng = self.eng
        ps = s.get("plan_state")
        if not ps:
            return False
        wants = {}
        for st in s["steps"]:
            if st["type"] == "op":
                wants.setdefault(st["dev"], set()).add(st["closed"])
        for did, v in ps.items():
            d = eng.sub.devs[did]
            if d.pending:
                continue
            if d.closed != v and d.closed not in wants.get(did, ()):
                return True
        return False

    def exec_next(self):
        s = self.session
        if not s or not s["guided"]:
            return
        for i, st in enumerate(s["steps"]):
            if not s["marks"][i]:
                break
        else:
            return
        eng = self.eng
        if st["type"] == "op":
            ok, msg = eng.command(st["dev"], st["closed"])
            self.statusBar().showMessage(msg, 4000)
        elif st["type"] == "line":
            eng.set_line(st["dev"], st["alive"])
        elif st["type"] == "tag":
            eng.set_tag(st["dev"], "LOTO – Manutenção")
        elif st["type"] == "untag":
            eng.remove_tag(st["dev"])
        elif st["type"] == "check":
            QMessageBox.information(self, "Verificação", "Ausência de tensão confirmada pelo detector."
                                    if self.step_done(st) else "ATENÇÃO: há tensão! Não prossiga.")
        self.focus_device(st.get("dev")) if st.get("dev") else None
        self.fill_all()
        self.session_update()

    def verify(self):
        s = self.session
        if not s:
            QMessageBox.information(self, "Verificação", "Inicie uma manutenção primeiro.")
            return
        rep = planner.isolation_report(self.eng.sub, self.eng.snapshot(), s["target"])
        ok = all(r[0] for r in rep)
        txt = "\n".join(("✔ " if r[0] else "✘ ") + r[1] for r in rep)
        txt += "\n\n" + ("LIBERADO para manutenção." if ok else "NÃO LIBERAR – há pendências de segurança.")
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Verificação de isolamento", txt)

    def start_return(self):
        s = self.session
        if not s:
            return
        s["phase"] = "return"
        s["done"] = False
        s["guided"] = True
        self.make_plan()

    def end_session(self, silent=False):
        self.session = None
        if self.ctx:
            self.ctx.hint = set()
        if hasattr(self, "lst_g"):
            self.lst_g.clear()
            self.lbl_g.setText("Nenhuma manutenção em andamento.\nUse o botão “Simular manutenção…”.")

    def _after_plan(self):
        pass


def run():
    import sys
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pal = app.palette()
    pal.setColor(pal.ColorRole.Window, QColor("#d9dcdf"))
    app.setPalette(pal)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

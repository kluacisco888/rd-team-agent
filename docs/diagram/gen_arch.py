#!/usr/bin/env python3
"""Generate the rd-team-agent architecture diagram (drawio mxfile).

Every coordinate is computed here; the script also checks box overlaps,
edges crossing unrelated boxes, and rough text fit, and exits 1 on problems.
Usage: gen_arch.py [--external] OUT.drawio

--external writes the version for readers outside the project team
(docs/architecture.md): no AWS account ID and no ownership badges; the box
colours alone tell the first version from later work. Layout is unchanged.
"""
import re
import sys
import xml.etree.ElementTree as ET

EXTERNAL = "--external" in sys.argv[1:]
ARGS = [a for a in sys.argv[1:] if a != "--external"]
if len(ARGS) != 1:
    sys.exit("usage: gen_arch.py [--external] OUT.drawio")
OUT = ARGS[0]


def t(internal, external):
    """Pick the text for the internal (note) or external (repo docs) version."""
    return external if EXTERNAL else internal


W, H = 1190, t(840, 810)

FONT = "fontColor=#1f2328;"
BASE = "rounded=1;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;fontSize=12;"
STYLE = {
    "v1": BASE + "spacingTop=4;arcSize=6;fillColor=#dae8fc;strokeColor=#1f6feb;strokeWidth=1.6;" + FONT,
    "stub": BASE + "spacingTop=4;arcSize=6;fillColor=#fff2cc;strokeColor=#bf8700;strokeWidth=1.4;dashed=1;dashPattern=5 4;" + FONT,
    "cust": BASE + "spacingTop=4;arcSize=8;fillColor=#ffffff;strokeColor=#57606a;strokeWidth=1.3;dashed=1;dashPattern=7 4;" + FONT,
    "later": BASE + "spacingTop=4;arcSize=8;fillColor=#f6f8fa;strokeColor=#8c959f;strokeWidth=1.2;dashed=1;dashPattern=2 3;fontColor=#57606a;",
    "inner": BASE + "spacingTop=3;arcSize=6;fillColor=#ffffff;strokeColor=#afb8c1;" + FONT,
    "tool": BASE + "spacingTop=3;arcSize=6;fillColor=#ffffff;strokeColor=#1f6feb;strokeWidth=1.2;" + FONT,
    "zone": "rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#f6f8fa;strokeColor=#d0d7de;verticalAlign=top;align=left;spacingLeft=10;spacingTop=2;fontStyle=1;fontSize=13;" + FONT,
    "pill": "rounded=1;arcSize=50;whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#8c959f;fontSize=12;" + FONT,
    "text": "text;html=1;align=left;verticalAlign=middle;whiteSpace=nowrap;fontSize=11;" + FONT,
    "textr": "text;html=1;align=right;verticalAlign=middle;whiteSpace=nowrap;fontSize=11;" + FONT,
    "tag": "text;html=1;align=center;verticalAlign=middle;whiteSpace=nowrap;fontSize=11;labelBackgroundColor=#f6f8fa;" + FONT,
    "num": "text;html=1;align=center;verticalAlign=middle;whiteSpace=nowrap;fontSize=12;fontStyle=1;fontColor=#1f6feb;",
}
BADGE = {"me": ("我做", "#1f6feb"), "cu": ("客户做", "#bc4c00"), "both": ("双方", "#8250df")}
EDGE = {
    "solid": "endArrow=classic;html=1;rounded=0;strokeColor=#57606a;strokeWidth=1.5;",
    "both": "endArrow=classic;startArrow=classic;html=1;rounded=0;strokeColor=#57606a;strokeWidth=1.5;",
    "stream": "endArrow=classic;html=1;rounded=0;strokeColor=#1a7f37;strokeWidth=1.8;",
    "kb": "endArrow=classic;html=1;rounded=0;strokeColor=#1f6feb;strokeWidth=1.6;",
    "aux": "endArrow=classic;html=1;rounded=0;strokeColor=#57606a;strokeWidth=1.3;dashed=1;dashPattern=4 3;",
    "later": "endArrow=classic;html=1;rounded=0;strokeColor=#8c959f;strokeWidth=1.3;dashed=1;dashPattern=5 4;",
    "bus": "endArrow=none;html=1;rounded=0;strokeColor=#8c959f;strokeWidth=1.3;dashed=1;dashPattern=5 4;",
}


def title(t):
    return f"<b style='font-size:13px'>{t}</b>"


def grey(t):
    return f"<font color='#666666' style='font-size:11px'>{t}</font>"


def num(n):
    return f"<b><font color='#1f6feb'>{n}</font></b>"


def br(*parts):
    return "<br>".join(parts)


cells = []      # (id, kind, value, style, geom)
boxes = {}      # id -> (x, y, w, h, role)   role: zone | container | box | badge | label


def vertex(cid, x, y, w, h, style, value="", role="box"):
    cells.append((cid, "v", value, style, (x, y, w, h)))
    boxes[cid] = (x, y, w, h, role)


def box(cid, x, y, w, h, kind, value, badge=None, role="box"):
    vertex(cid, x, y, w, h, STYLE[kind], value, role)
    if badge and not EXTERNAL:
        text, color = BADGE[badge]
        vertex("bd_" + cid, x + w - 54, y - 9, 46, 18,
               f"rounded=1;arcSize=50;html=1;fillColor={color};strokeColor=none;fontColor=#ffffff;fontSize=11;fontStyle=1;",
               text, "badge")


def label(cid, x, y, w, h, value, kind="text"):
    vertex(cid, x, y, w, h, STYLE[kind], value, "label")


edges = []      # (id, style, points)


def edge(cid, kind, *pts):
    edges.append((cid, EDGE[kind], list(pts)))


# ---------------- layout ----------------
# legend (top)
lx = 20
for key, text, tw in [("v1", "第一版就做", 70), ("stub", "先用替身跑通", 84),
                      ("cust", t("要客户环境或授权", "需要外部环境或授权"), t(108, 100)),
                      ("later", "后期再做", 60)]:
    vertex("lg_" + key, lx, 12, 18, 12, STYLE[key], "", "legend")
    label("lt_" + key, lx + 22, 10, tw, 16, text)
    lx += 22 + tw + 12
edge("lg_stream", "stream", (lx, 18), (lx + 30, 18))
label("lt_stream", lx + 36, 10, 112, 16, "流式返回 / 取回结果")
label("lt_num", lx + 160, 10, 210, 16, f"{num('①-⑦')} " + t("见笔记「3. 调用流程」", "见「调用流程」"))

# zones
ZT = 40
box("zA", 10, ZT, 225, 350, "zone", "入口", role="zone")
box("zI", 10, 405, 225, 395, "zone", "身份提供方", role="zone")
box("zB", 265, ZT, 515, 760, "zone", t("AWS 954976326201", "AWS 账号") + " · us-east-1 · AgentCore", role="zone")
box("zC", 815, ZT, 365, 760, "zone", "工具与数据", role="zone")

# --- zone A: entry
AX, AW = 25, 195
vertex("user", AX, 64, AW, 38, STYLE["pill"], br(title("研发同学"), grey(t("演示：我用 3 个测试账号", "演示：3 个测试账号"))))
box("app", AX, 120, AW, 150, "v1", br(
    title("macOS App（SwiftUI）"),
    "· 登录（授权码 + PKCE）",
    "· 会话列表、流式对话",
    "· 附件 .md / PDF（本机提取）",
    "· 生成进度（轮询）",
    "· 保存前确认卡片（T2）",
    "· 本机另存一份 PRD",
    grey("不放 AWS 密钥")), badge="me")
box("teams", AX, 290, 125, 50, "later", br(title("Teams / Web"), grey("后期可选入口")), badge="cu")
app = boxes["app"]
edge("e_user", "solid", (AX + AW // 2, 102), (AX + AW // 2, app[1] - 2))

# --- zone I: identity provider
box("idp", AX, 440, AW, 120, "stub", br(
    title("Cognito 用户池（替身，R1）"),
    "签发 JWT，关闭自助注册",
    t("3 个测试账号，密码我自己设", "演示用 3 个测试账号"),
    grey(t("我：先建 Cognito 替身", "第一版先用 Cognito 代替公司 IdP")),
    grey(t("客户：以后在 Entra 或 Okta 注册应用，直接接入或联邦到 Cognito",
           "以后接 Entra 或 Okta：直接接入或联邦到 Cognito"))), badge="both")
idp = boxes["idp"]
LOGIN_X = AX + AW - 15
edge("e_login", "both", (LOGIN_X, app[1] + app[3]), (LOGIN_X, idp[1] - 2))
label("n1", LOGIN_X - 98, 352, 92, 16, f"{num('①')} 登录拿 JWT", "textr")

# --- zone B: AgentCore
RX, RW = 280, 290           # runtime container
TX, TW = RX + 14, RW - 28   # tools inside
CX, CW = 600, 172           # right column
box("rt", RX, 90, RW, 326, "v1", br(
    title("AgentCore Runtime（新建，R6）"),
    grey("入站 JWT；用户 ID 只取 JWT 的 sub；角色 R5")), badge="me", role="container")
box("orch", TX, 138, TW, 50, "inner", br(
    title("Strands 编排 Agent（Opus 5.5）"),
    grey("在对话里追问需求，决定调哪个工具")))
box("gen", TX, 202, TW, 66, "tool", br(
    title("工具 1 generate_prd（后台任务）"),
    grey("复用现有生成和校验代码"),
    grey("T1：只出草稿，不写外部系统")))
box("stat", TX, 280, TW, 44, "tool", br(
    title("工具 2 get_prd_status"),
    grey("查生成进度（异步任务状态）")))
box("save", TX, 336, TW, 66, "tool", br(
    title("工具 3 save_prd_to_github（T2）"),
    grey("App 里确认后才执行"),
    grey("用 R4 的令牌写进 PRD 文档库")))
rt, orch, gen, save = boxes["rt"], boxes["orch"], boxes["gen"], boxes["save"]
edge("e_orch", "solid", (TX + TW // 2, orch[1] + orch[3]), (TX + TW // 2, gen[1] - 2))

box("mem", CX, 90, CW, 92, "v1", br(
    title("Memory（R2）"),
    grey("短期：会话记录 30 天"),
    grey("长期：偏好、事实、摘要"),
    grey("命名空间按 actorId 分开")), badge="me")
box("s3", CX, 212, CW, 76, "v1", br(
    title("结果 S3 桶（R3）"),
    grey("results/用户/会话/任务"),
    grey("只经 Runtime 读取")), badge="me")
box("idk", CX, 318, CW, 76, "v1", br(
    title("Identity 凭证（R4）"),
    grey("GitHub 细粒度令牌"),
    grey("只能读写 PRD 文档库")), badge="me")
box("obs", CX, 436, CW, 56, "v1", br(
    title("Observability"),
    grey("日志和链路追踪，第一版就开")), badge="me")
mem, s3, idk, obs = boxes["mem"], boxes["s3"], boxes["idk"], boxes["obs"]

LATER_W = CX + CW - RX
box("gw", RX, 520, LATER_W, 62, "later", br(
    title("Gateway + Policy（后期）"),
    grey("统一 MCP 端点；Policy 在每次工具调用前允许或拒绝"),
    grey("接第一个外部系统时再建")), badge="cu")
box("idn", RX, 602, LATER_W, 52, "later", br(
    title("Identity 出站 OAuth / OBO（后期）"),
    grey("以用户本人身份换 Jira、GitHub 等的 token")), badge="cu")
box("newres", RX, 676, LATER_W, 100, "inner", br(
    title(t("第一版要新建的资源（见第 9.1 节）", "第一版用到的资源（见「资源清单」）")),
    "R1 Cognito · R2 Memory · R3 结果桶 · R4 Identity 凭证",
    "R5 Runtime 角色 · R6 Runtime · R7 知识库" + t("（复用）", ""),
    "R8 示例代码仓 · R9 PRD 文档库",
    grey(t("每项新建前先审批；现有 SigV4 Runtime 和 Mac app 不动",
           "Gateway、出站 OAuth 等后期资源不在此列"))))
gw, idn = boxes["gw"], boxes["idn"]

# --- zone C: tools and data
KX, KW = 830, 335
HW = 160                      # half-width boxes
KX2 = KX + KW - HW
box("kb", KX, 90, KW, 90, "v1", br(
    title(t("知识库 MGDYVQP2FE（复用，R7）", "知识库（R7）")),
    grey("附件：自定义数据源，按 task_id 隔离"),
    grey("示例代码：S3 数据源，新的 project_id"),
    grey("检索：本次附件 + 同一提交的代码")), badge="me")
box("prdrepo", KX, 216, HW, 80, "stub", br(
    title("PRD 文档库（R9）"),
    grey("GitHub 私有仓"),
    grey("rd-team-agent-prds"),
    grey(t("代替客户的 SharePoint", "演示中代替 SharePoint"))), badge="me")
box("sample", KX2, 216, HW, 80, "stub", br(
    title("示例代码仓（R8）"),
    grey("GitHub 私有仓"),
    grey("rd-team-agent-sample-app"),
    grey("管理后台，还没有导入功能")), badge="me")
box("sp", KX, 318, HW, 66, "cust", br(
    title(t("SharePoint（客户）", "SharePoint")),
    grey("以后写回最终 PRD"),
    grey(t("需客户授权写权限", "需要写权限授权"))), badge="cu")
kb, prdrepo, sample, sp = boxes["kb"], boxes["prdrepo"], boxes["sample"], boxes["sp"]

TOOLS = [
    ("jira", 470, 50, "cust", br(title("Jira / Confluence · Rovo MCP"),
                                 grey(t("仅 Atlassian Cloud；客户提供系统和 OAuth 授权",
                                        "仅 Atlassian Cloud；需要系统和 OAuth 授权")))),
    ("code", 532, 50, "cust", br(title(t("客户代码库", "代码库") + " · GitHub / GitLab MCP"),
                                 grey(t("客户提供仓库权限", "需要仓库权限")))),
    ("splk", 594, 50, "cust", br(title("Splunk / New Relic · 官方 MCP"),
                                 grey(t("客户提供环境和账号", "需要环境和账号")))),
    ("appr", 656, 50, "later", br(title("审批服务（自建，后期）"), grey("签发审批编号，T3 动作必须带编号"))),
    ("cicd", 730, 54, "cust", br(title("CI/CD · Jenkins / Argo CD"),
                                 grey(t("客户提供；第 3 期 staging 部署", "第 3 期 staging 部署")))),
]
for cid, y, h, kind, value in TOOLS:
    box(cid, KX, y, KW, h, kind, value, badge="cu")


def mid_y(b):
    return b[1] + b[3] // 2


# ---------------- edges ----------------
# ② App -> Runtime (JWT + attachments)
edge("e_call", "solid", (AX + AW, mid_y(orch)), (TX - 2, mid_y(orch)))
label("n2", 240, mid_y(orch) - 18, 20, 16, "②", "num")
# ⑤ streaming reply / fetch PRD (Runtime -> App)
Y5 = 230
edge("e_sse", "stream", (RX, Y5), (AX + AW + 2, Y5))
label("n5", 240, Y5 - 18, 20, 16, "⑤", "num")
# ⑥ confirm card -> save tool
X6 = 258
edge("e_confirm", "solid", (AX + AW, 255), (X6, 255), (X6, mid_y(save)), (TX - 2, mid_y(save)))
label("n6", X6 - 20, 300, 16, 16, "⑥", "num")
# ③ JWT validation against Cognito discovery URL
X3, Y3 = 246, 395
edge("e_jwt", "aux", (RX, Y3), (X3, Y3), (X3, idp[1] + 60), (AX + AW + 2, idp[1] + 60))
label("n3", X3 + 2, 440, 14, 16, "③", "num")
# ③ orchestrator <-> Memory
GAP_X = (RX + RW + CX) // 2           # lane between runtime and right column
edge("e_mem", "solid", (TX + TW, mid_y(orch)), (CX - 2, mid_y(orch)))
label("n3m", GAP_X - 10, mid_y(orch) - 18, 20, 16, "③", "num")
# ④ generate_prd -> results bucket
Y4S = 250
edge("e_s3", "solid", (TX + TW, Y4S), (CX - 2, Y4S))
label("n4s", GAP_X - 10, Y4S - 18, 20, 16, "④", "num")
# ④ generate_prd -> knowledge base (write attachments, retrieve)
LANE_KB = (mem[1] + mem[3] + s3[1]) // 2 - 4          # gap between Memory and S3
X_KB = 805
edge("e_kb", "kb", (TX + TW, gen[1] + 13), (GAP_X, gen[1] + 13), (GAP_X, LANE_KB),
     (X_KB, LANE_KB), (X_KB, mid_y(kb)), (KX - 2, mid_y(kb)))
label("n4k", 650, LANE_KB - 7, 100, 14, f"{num('④')} 写附件、检索", "tag")
# ⑥ save tool -> Identity (token) and -> PRD repo
Y6T = 356
edge("e_tok", "aux", (TX + TW, Y6T), (CX - 2, Y6T))
LANE_W = (idk[1] + idk[3] + obs[1] - 9) // 2          # gap between Identity and Obs badge
X_W = 797
edge("e_write", "solid", (TX + TW, save[1] + 50), (GAP_X, save[1] + 50), (GAP_X, LANE_W),
     (X_W, LANE_W), (X_W, mid_y(prdrepo)), (KX - 2, mid_y(prdrepo)))
label("n6w", 650, LANE_W - 7, 70, 14, f"{num('⑥')} 写入", "tag")
# Observability
X_OBS = TX + TW - 16
edge("e_obs", "aux", (X_OBS, rt[1] + rt[3]), (X_OBS, mid_y(obs)), (CX - 2, mid_y(obs)))
# C2: sample repo synced into KB by commit
X_C2 = KX2 + HW // 2
edge("e_c2", "aux", (X_C2, sample[1]), (X_C2, kb[1] + kb[3] + 2))
label("t_c2", X_C2 - 98, kb[1] + kb[3] + 8, 92, 14, t("按提交同步（C2）", "按提交同步"), "tag")
# PRD repo -> SharePoint later
X_SP = KX + 40
edge("e_sp", "later", (X_SP, prdrepo[1] + prdrepo[3]), (X_SP, sp[1] - 2))
label("t_sp", X_SP + 6, prdrepo[1] + prdrepo[3] + 4, 56, 14, "以后换成", "tag")
# ⑦ later: Runtime -> Gateway -> outbound identity, bus to customer tools
X7 = RX + 50
edge("e_gw", "later", (X7, rt[1] + rt[3]), (X7, gw[1] - 2))
label("n7", X7 + 4, (rt[1] + rt[3] + gw[1]) // 2 - 8, 20, 16, "⑦", "num")
edge("e_idn", "later", (RX + 170, gw[1] + gw[3]), (RX + 170, idn[1] - 2))
X_BUS = 807
bus_ys = [mid_y(boxes[c]) for c in ("jira", "code", "splk", "appr")]
edge("bus_in", "bus", (gw[0] + gw[2], mid_y(gw)), (X_BUS, mid_y(gw)))
edge("bus", "bus", (X_BUS, bus_ys[0]), (X_BUS, bus_ys[-1]))
for c, y in zip(("jira", "code", "splk", "appr"), bus_ys):
    edge("b_" + c, "later", (X_BUS, y), (KX - 2, y))
appr, cicd = boxes["appr"], boxes["cicd"]
X_CI = KX + 170
edge("e_ci", "solid", (X_CI, appr[1] + appr[3]), (X_CI, cicd[1] - 2))
label("t_ci", X_CI + 6, appr[1] + appr[3] + 3, 50, 16, "批准后")

# ownership legend (bottom); the external version has no ownership badges
lx = 20
OWNERS = [] if EXTERNAL else [("me", "我（AWS 侧）负责", 110), ("cu", "客户提供或负责", 100),
                              ("both", "两边各做一部分，见框内「我：」「客户：」", 260)]
for key, text, tw in OWNERS:
    badge_text, color = BADGE[key]
    vertex("lgo_" + key, lx, 812, 46, 18,
           f"rounded=1;arcSize=50;html=1;fillColor={color};strokeColor=none;fontColor=#ffffff;fontSize=11;fontStyle=1;",
           badge_text, "legend")
    label("lgot_" + key, lx + 52, 813, tw, 16, text)
    lx += 52 + tw + 18


# ---------------- checks ----------------
problems = []


def inside(a, b, pad=0):
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    return ax >= bx - pad and ay >= by - pad and ax + aw <= bx + bw + pad and ay + ah <= by + bh + pad


def overlap(a, b):
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


solid = {k: v for k, v in boxes.items() if v[4] in ("box", "container", "badge", "label", "legend")}
for i, (ka, a) in enumerate(solid.items()):
    for kb_, b in list(solid.items())[i + 1:]:
        if not overlap(a, b):
            continue
        if inside(a, b) or inside(b, a):
            if "container" in (a[4], b[4]):
                continue
        if a[4] == "badge" and ka == "bd_" + kb_ or b[4] == "badge" and kb_ == "bd_" + ka:
            continue
        problems.append(f"overlap: {ka} {a[:4]} / {kb_} {b[:4]}")
    if a[0] < 0 or a[1] < 0 or a[0] + a[2] > W or a[1] + a[3] > H:
        problems.append(f"out of canvas: {ka}")


def seg_hits(p, q, b):
    x0, y0 = p
    x1, y1 = q
    bx, by, bw, bh = b[0] + 1, b[1] + 1, b[2] - 2, b[3] - 2
    if x0 == x1:
        return bx < x0 < bx + bw and min(y0, y1) < by + bh and max(y0, y1) > by
    return by < y0 < by + bh and min(x0, x1) < bx + bw and max(x0, x1) > bx


for eid, _, pts in edges:
    ends = [pts[0], pts[-1]]
    for (p, q) in zip(pts, pts[1:]):
        if p[0] != q[0] and p[1] != q[1]:
            problems.append(f"diagonal segment in {eid}")
        for k, b in boxes.items():
            if b[4] in ("zone", "container", "legend"):
                continue
            near_end = any(b[0] - 4 <= ex <= b[0] + b[2] + 4 and b[1] - 4 <= ey <= b[1] + b[3] + 4 for ex, ey in ends)
            if near_end and b[4] != "label":
                continue
            if b[4] == "label" and STYLE["tag"] == [c for c in cells if c[0] == k][0][3]:
                continue  # tags sit on their line by design (with background)
            if seg_hits(p, q, b):
                problems.append(f"edge {eid} crosses {k}")


def text_width(s, size):
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2e80:
            w += size
        elif ch == " ":
            w += size * 0.3
        else:
            w += size * 0.58
    return w


for cid, kind, value, style, (x, y, w, h) in cells:
    if boxes[cid][4] not in ("box", "container") or not value:
        continue
    total = 0
    for line in value.split("<br>"):
        size = 13 if "font-size:13px" in line else 11 if "font-size:11px" in line else 12
        plain = re.sub(r"<[^>]+>", "", line)
        lw = text_width(plain, size)
        avail = w - 10 - 6
        n = max(1, -(-int(lw) // int(avail)))
        if n > 1 and "font-size:11px" not in line:
            problems.append(f"text wraps in {cid}: {plain!r} ({lw:.0f}px > {avail}px)")
        total += n * size * 1.25
    need = total + 4 + 4
    limit = h if boxes[cid][4] == "box" else 9999
    if need > limit:
        problems.append(f"text too tall in {cid}: need {need:.0f}px, box {h}px")


# ---------------- emit ----------------
DIAGRAM_ID = t("rd-team-agent-architecture", "rd-team-agent-architecture-external")
mx = ET.Element("mxfile", host="Obsidian", type="embed")
dg = ET.SubElement(mx, "diagram", id=DIAGRAM_ID, name="研发团队 Agent 架构")
gm = ET.SubElement(dg, "mxGraphModel", dx=str(W), dy=str(H), grid="0", gridSize="10", guides="1", tooltips="1",
                   connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=str(W),
                   pageHeight=str(H), background="#ffffff", math="0", shadow="0")
root = ET.SubElement(gm, "root")
ET.SubElement(root, "mxCell", id="0")
ET.SubElement(root, "mxCell", id="1", parent="0")
for cid, kind, value, style, (x, y, w, h) in cells:
    c = ET.SubElement(root, "mxCell", id=cid, value=value, style=style, vertex="1", parent="1")
    ET.SubElement(c, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
for eid, style, pts in edges:
    c = ET.SubElement(root, "mxCell", id=eid, style=style, edge="1", parent="1")
    g = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
    ET.SubElement(g, "mxPoint", x=str(pts[0][0]), y=str(pts[0][1]), **{"as": "sourcePoint"})
    ET.SubElement(g, "mxPoint", x=str(pts[-1][0]), y=str(pts[-1][1]), **{"as": "targetPoint"})
    if len(pts) > 2:
        arr = ET.SubElement(g, "Array", **{"as": "points"})
        for px, py in pts[1:-1]:
            ET.SubElement(arr, "mxPoint", x=str(px), y=str(py))
head = ('<mxfile host="Obsidian" type="embed">\n'
        f'  <diagram id="{DIAGRAM_ID}" name="研发团队 Agent 架构">\n'
        '    ' + ET.tostring(gm, encoding="unicode").split("<root>")[0].rstrip() + '\n'
        '      <root>')
body = ["        " + ET.tostring(c, encoding="unicode").strip() for c in root]
xml = "\n".join([head] + body + ["      </root>", "    </mxGraphModel>", "  </diagram>", "</mxfile>"])
ET.fromstring(xml)  # must parse

if problems:
    print("\n".join(problems))
    sys.exit(1)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(xml + "\n")
print(f"ok: {len(cells)} vertices, {len(edges)} edges, {len(xml.splitlines())} lines -> {OUT}")

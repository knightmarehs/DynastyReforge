#!/usr/bin/env python3
"""把游戏数值汇总成前端能直接吃的结构化配置。

**这是数值的唯一来源。** 前端不许在 JS 里硬编码任何数值或成长率；
公式集中在 `game/src/systems/formulas.js`，那里只放**算法**，
所有**数**都从这份配置来。改数值改这里重跑，不要改 JS。

四个来源（详见 `docs/专题/数值体系.md`）：

| 段 | 来自 | 说明 |
|---|---|---|
| `characters` | 🅐 `public/Api.enc` | 我方七人出场属性、剩余五内、初始绝学 |
| `growth` | 🅐 `exe/RPG.exe` 成长表 0x46c1b0 | 300块 版仍读配套资料开头那张表 |
| `levelup` | 🅐 `public/Levelup.enc` | 累计历练门槛，101 档 |
| `wuneiResist` | 自有常量 `WUNEI_RESIST` | 五内→抗性系数，**原版与本补丁两张**；原取自配套资料6－五魂化蕴，已用存档验证 |
| `skillUnlock` | 🅐 `public/Magictb.enc` | 绝学习得条件，133 条 |
| `equipment` `items` `skills` | 🅑 `data/*.json` | 原样带过来，只做数值化清洗 |

用法:
    python3 export_gamedata.py [-o game/public/assets/data]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class 版本:
    """一个游戏安装。`enc` 全部按版本切换 —— 布局一样，数值不一样。"""

    名: str
    multimedia: Path


#: 两个安装。**结构完全相同，数值大改** —— 逐表对照见 `docs/专题/官方版对照.md`。
#: 官方版是 300块 的真子集（绝学 500/1482、物品 700/1882、遇敌组 334/2915）。
VERSIONS = {
    "官方": 版本("官方", Path.home() / "Game/幽城幻剑录/Dynasty/Castle/multimedia"),
    "300块": 版本("300块", Path.home() / "Game/幽城幻剑录游戏及资料"
                          "/mod-300块建议玩这里面的这个版本/Castle/multimedia"),
}

#: **默认导官方版** —— 复刻以原版为准，300块 只作对照。
SRC = VERSIONS["官方"]

#: 简体名表所在目录（简体补丁的一部分，见 `simplified_index`）。
NAMES_DIR = ROOT / "data"

#: ⚠️ **配套资料只有 300块 那一份**，官方版游戏目录里根本没有这个目录
#: （它自带的是 `精品攻略集/`，格式是 chm 与 Access 库，另说）。
#: 所以 `read_txt()` **不跟着版本切**，它永远读 300块 的配套资料。
#: 凡是经它来的数（成长率、五内抗性系数）都要在产物里标明版本，别当成官方值。
ZL = VERSIONS["300块"].multimedia.parent / "配套资料"

#: 我方可上场的七人，顺序即**战斗角色代码 − 1**（立绘/姓名/队伍条小人都按这个取帧）。
PARTY = ("夏侯仪", "冰璃", "封铃笙", "慕容璇玑", "古伦德", "葛云衣", "霍雍")

#: ⚠️ 配套资料里写「封**玲**笙」，游戏与五魂化蕴写「封**铃**笙」。统一成后者。
NAME_FIX = {"封玲笙": "封铃笙", "萧熇": "蕭熇"}

#: 五内与五外的字段名。**「五外」是原作叫法**，与「五内」对仗，不要叫「五柱」。
WUNEI = ("迅", "烈", "神", "魔", "魂")
WUWAI = ("膂力", "灵力", "体魄", "迅捷", "机运")
RESIST = ("焚火", "冰凛", "雷荧", "烨光", "魔厉", "化相", "析魂", "外法")

#: 这些常数都是**实测**得来、资料里没写的，集中放一处便于改。出处见 `docs/专题/数值体系.md`。
CONSTANTS = {
    # ⚠️ **每次升级得到的五内点数**（五内页盘中央「蘊魄」的增量）。
    # `Api.enc` 里我方六人**没有任何公共字段的值是 3**（取值全同的 130 个偏移里
    # 非零的只有 100 和 1），配套资料也只给了召唤兽的「五内成长３」。
    # 这个数**大概率写死在程序里**，此处按用户实机确认的值填。
    "五内每级": 3,
    "抗性基准": 100,
    "抗性说明": "内禀抗性 = 抗性基准 + Σ(五内点数 × 系数)。两个版本各 8/8 验证过",
    # ⭐ **命中 = 命中基数 + 装备命中补正。机运不参与。**（2026-09-12 订正）
    #
    # 判据是 9 个采样，全部「原作显示值 − 装备命中补正 = 100」，无一例外：
    #
    #   Save008 五人（用户报的原作值）  机运 170/165/80/234/125
    #                                  命中 130/120/115/140/120
    #                                  补正  30/ 20/ 15/ 40/ 20  → 差全是 100
    #   Save009 霍雍                    机运 400 补正 60 命中 160 → 100
    #   200 块三张同装备不同等级        机运 74/158/188 补正 30 命中 130 → 100
    #
    # ⚠️ **曾经写成「基数 90 + 机运/4，上限 130」**，那是把「三张同装备不同机运
    # 恒为 130」读成了"撞上限"，其实是**机运本来就不影响它**。编出来的 130
    # 一遇到霍雍（补正 60，原作 160）就露馅。
    #
    # 🟡 **边界**：官方版采样的机运全部 ≥80，所以严格说只证明了「机运 ≥80 时不参与」。
    # `min(90+⌊机运/4⌋,100)+补正` 在这些点上给出相同结果，要区分得有机运 <40 的采样。
    #
    # ⚠️ 配套资料那句 `攻方命中 ＝ 90 + 装备命中补正 + 机运/4` 只对得上
    # **300 块 MOD** 的两张低级截图（机运 50/55 → 112/113）。那版改过公式，
    # 与「五内抗性系数」一样要分版本 —— 真要支持 MOD 版时照那张表的做法分表。
    "命中基数": 100,
    "命中说明": "命中 = 命中基数 + 装备命中补正。机运不参与（9 个官方/200块采样全中）",
    "闪避机运除数": 4,
    "护禦体魄除数": 2,
    "命极成长基数": 4, "命极成长体魄除数": 10,
    "气极成长基数": 3, "气极灵力系数": 2 / 3, "气极成长除数": 25,
    "成长随机上限": 2,
    "成长随机说明": "命极/气极公式里的 +0~2。五外成长另有区间，见 growth.range",
    "五外柱上限": 1000,
    "五外柱上限说明": "只影响状态页柱子画多满，不影响数值。量法见 export_menu_ui.FILL_SLOTS",
}

#: 开局装备。
#: ⚠️ **不是从数据里来的，是照 300 块实机开局截图填的** —— `characters.json` 的
#: 「装备」字段写的是「无」，而实际开局带这两件。真正的开局存档在
#: `Save/NewGame.TSF`，格式未解（`docs/判据/数据链路.md` §8.6），解开之后应当换成它。
START_GEAR = {
    "夏侯仪": {"兵刃": "护身匕首", "护甲": "布袍"},
}


def half(text) -> str:
    """全角转半角。配套资料里的数字、小数点、正负号**全是全角**。

    ⚠️ 范围必须覆盖整个 `！`(U+FF01) ~ `～`(U+FF5E)，**不能只写 `０`~`ｚ`**：
    全角句点 `．`(U+FF0E) 与正负号 `＋－`(U+FF0B/U+FF0D) 都在 `０`(U+FF10) **之前**，
    漏掉它们会让 `－１．５` 被截成 `-1` —— 五魂化蕴的系数集体丢掉小数位，
    而且丢得很隐蔽（总和从 −4 变成 −3）。这个坑踩过一次。
    """
    return "".join(chr(ord(c) - 0xFEE0) if "！" <= c <= "～" else c for c in str(text).strip())


def as_num(value):
    """能转成数就转，转不了返回 None。⚠️ 有些字段是「2000（算上装备补正）」这种。"""
    t = half(value)
    # ⚠️ `[+-]?` 的 `+` 不能漏：五魂化蕴的系数一半是 `＋１．５` 这种带正号的，
    # 只写 `-?` 会让所有正系数解析失败（表现为总和只剩负项，很隐蔽）。
    m = re.match(r"^[+-]?\d+(\.\d+)?", t)
    if not m:
        return None
    n = float(m.group(0))
    return int(n) if n == int(n) else n


def read_txt(pattern: str) -> str:
    """配套资料的编码不统一，按「解出来汉字最多」自动选。"""
    path = next(ZL.glob(pattern))
    raw = path.read_bytes()
    best = None
    for enc in ("gb18030", "utf-16le", "utf-16", "big5", "utf-8"):
        try:
            text = raw.decode(enc)
        except Exception:
            continue
        score = sum("一" <= c <= "鿿" for c in text)
        if best is None or score > best[0]:
            best = (score, text)
    return best[1]


def decrypt(name: str) -> bytes:
    """`明文[i] = 密文[i] XOR (i % 255)`。⚠️ 是 255 不是 256，见 §2.7。"""
    raw = np.frombuffer((SRC.multimedia / "public" / name).read_bytes(), dtype=np.uint8)
    key = (np.arange(len(raw), dtype=np.uint64) % 255).astype(np.uint8)
    return (raw ^ key).tobytes()


#: `Api.enc` 的角色/敌人属性表：**1482 条 × 848 字节，下标即角色代码**。
#:
#: 命/气/五外由 339 条全表对齐验出（97.6%~99.4%）；位阶、历练、五内靠我方
#: 角色人工对照 —— **冰璃那条把五内顺序定死了**，因为她五个值互不相同
#: （迅12 烈25 神10 魔15 魂15），三人 3/3 吻合。
#:
#: ⚠️ 位阶全表吻合率只有 11%，**不是没找到** —— 那 214 条样本大多是敌人，
#: 配套 txt 里敌人的「位阶」是另一种口径。我方 3/3 全对，采信 +32。
API_STRIDE = 848
API_FIELDS = {
    32: "位阶", 36: "已有历练", 44: "命极", 48: "命", 52: "气极", 56: "气",
    60: "膂力", 64: "体魄", 68: "灵力", 72: "迅捷", 76: "机运",
    84: "迅", 88: "烈", 92: "神", 96: "魔", 100: "魂",
    400: "基础必杀",
}


#: `Shopitem.dat` 的商店售货表：**63 条 × 648 字节**。
#:
#: ⚠️ **这个文件没有加密**（后缀是 `.dat` 不是 `.enc`）。对它做 `XOR (i%255)`
#: 反而把数据搞乱 —— 我一开始就是这么白找了一轮。
#:
#: 记录布局（判据：商店 #21 与 MOD 工具界面上「5大城市3期 武器铺」那一屏
#: 逐项相同 —— 护身匕首200 / 古铜怀刀400 / 長劍350 / 青釭劍800 / 白絹索帶240…）::
#:
#:     +0    商店种类      +4  商品种类数
#:     +28   商品代码 ×50
#:     +228  商品售价 ×50
#:     +428  单次可购买数 ×50   （0 = 不限）
SHOP_STRIDE = 648
SHOP_COUNT_OFF = 4
SHOP_CODE_OFF = 28
SHOP_PRICE_OFF = 228
SHOP_LIMIT_OFF = 428
SHOP_SLOTS = 50


def load_shops() -> list[dict]:
    """`Shopitem.dat` → 每家店卖什么、卖多少钱。**不解密**，见上面的说明。"""
    data = (SRC.multimedia / "public" / "Shopitem.dat").read_bytes()
    out = []
    for i in range(len(data) // SHOP_STRIDE):
        base = i * SHOP_STRIDE
        u32 = lambda off: int.from_bytes(  # noqa: E731
            data[base + off:base + off + 4], "little")
        n = min(u32(SHOP_COUNT_OFF), SHOP_SLOTS)
        goods = [
            {
                "物品代码": u32(SHOP_CODE_OFF + k * 4),
                "售价": u32(SHOP_PRICE_OFF + k * 4),
                "单次可购买数": u32(SHOP_LIMIT_OFF + k * 4),
            }
            for k in range(n)
        ]
        out.append({"编号": i, "商店种类": u32(0), "商品": goods})
    return out


#: 战斗遇敌：**「遇敌群」与「遇敌组」是两回事**，两张表分开存。
#:
#: * **遇敌组**（`LayoutTeam.ENC`，2915 × 508）—— 一场具体的仗：出场哪几个敌人、
#:   站在哪、掉多少钱、掉什么战利品。
#: * **遇敌群**（`Layoutgr.enc`，776 × 508）—— 一个地点的遭遇池：随机从它列出的
#:   十个遇敌组里挑一个，外加战斗背景地图、背景音乐、逃跑概率。
#:
#: 我一度把两者混为一谈，拿「遇敌组」的截图去核对「遇敌群」的记录，
#: 自然对不上，还误判成「工具打开的是别的版本」。**先分清概念再找数据。**
#:
#: 两张表的偏移都用 MOD 工具界面上代码 0002 那一屏逐项核对过，全中。
GROUP_STRIDE = 508          #: 遇敌组（LayoutTeam.ENC）
SWARM_STRIDE = 508          #: 遇敌群（Layoutgr.enc）
NAME_OFF = 68               #: 两张表的 Big5 名称都在 +68


def _big5(blob: bytes) -> str:
    try:
        return blob.split(b"\x00")[0].decode("big5").strip()
    except UnicodeDecodeError:
        return ""


def load_encounter_groups() -> list[dict]:
    """遇敌组：一场具体的仗。判据见 GROUP_STRIDE 上方。"""
    data = decrypt("LayoutTeam.ENC")
    out = []
    for i in range(len(data) // GROUP_STRIDE):
        b = i * GROUP_STRIDE
        u32 = lambda o: int.from_bytes(data[b + o:b + o + 4], "little")  # noqa: E731
        n = min(u32(0), 8)
        out.append({
            "编号": i,
            "名称": _big5(data[b + NAME_OFF:b + NAME_OFF + 40]),
            "出场敌人数": n,
            "敌人": [{"代码": u32(4 + k * 4), "位置": u32(36 + k * 4)} for k in range(n)],
            "掉落金钱下限": u32(116),
            "掉落金钱浮动": u32(120),
            "战利品": [{"代码": u32(128 + k * 4), "概率": u32(144 + k * 4)}
                       for k in range(min(u32(124), 3))],
        })
    return out


#: 遇敌群 `+136` = **战斗音乐档次**（2026-09-13 标定）。
#:
#: 判据：508 字节里逐个 u32 扫过全部 250 条，能当索引用的小值域字段**只有它**，
#: 取值 0/1/2/3，43 条非零。把非零那 43 条列出来，分布随剧情强度单调上升：
#:
#:   1 = 剧情战（冰璃登場 / 沙漠營地決戰 / 磐沙堡決戰 / 石塔決戰 / 巨蟲A~E…）21 条
#:   2 = 强敌（封豨 / 羅喉降臨 / 最強兵器 / 陰陽人形 / 高昌決戰…）17 条
#:   3 = **最終決戰 A1/A2/B1/B2/C，五条一条不漏**，再没有别的            5 条
#:
#: 「值 3 恰好只落在五场最终决战上」是这条里最硬的一段。
#:
#: ⚠️ **档次 → 曲子文件是推断**（🟡，已登记台账）：`fight/Audio/Mp3/` 正好七首
#: `Music0<档次><变体>`（000/010/020/030/031/032/033），四个十位号对上四档，
#: 时长也随档次上升（24s → 73s → 35s → 139~267s）。要在原版里听三场才能钉死。
#:
#: ⚠️ **没有任何字段直接写曲名** —— 遇敌组里没有、脚本 `play_audio` 只有
#: kind=0/1 两类不切战斗曲、`Miscinfo.enc` 解出来几乎全零。都查过了。
SWARM_BGM_TIER_OFF = 136


def load_encounter_swarms() -> list[dict]:
    """遇敌群：一个地点的遭遇池 + 战斗背景 + 战斗音乐 + 逃跑概率。

    ⚠️ **`op55 battle` 的 `swarm` 指的是这张表，不是遇敌组**（2026-09-13 订正）。
    判据：`MP1301A/B/C`（月牙泉底冰窟）`swarm=16` → 群16「月牙泉下冰窟B」，
    而组16 是「一般16—高昌沙漠迷陣」；`MP1600A`（往拓渾隧道入口）`swarm=11`
    → 群11「拓渾古垣迴道A」，组11 是「石塔B」。结构判据更硬：一场仗必须知道
    「在哪打、能不能逃、放什么曲子」，而这三项**只存在于遇敌群**。
    剧情战的群与组同号同名，所以读错也不报错 —— 它就是这么躲过去的。
    """
    data = decrypt("Layoutgr.enc")
    out = []
    for i in range(len(data) // SWARM_STRIDE):
        b = i * SWARM_STRIDE
        u32 = lambda o: int.from_bytes(data[b + o:b + o + 4], "little")  # noqa: E731
        out.append({
            "编号": i,
            "名称": _big5(data[b + NAME_OFF:b + NAME_OFF + 40]),
            "遇敌组": [u32(28 + k * 4) for k in range(min(u32(24), 10))],
            "战斗背景地图": [u32(112 + k * 4) for k in range(min(u32(108), 2))],
            "战斗音乐": u32(SWARM_BGM_TIER_OFF),
            "逃跑概率": u32(156),
            "附加遇敌组": [u32(220 + k * 4) for k in range(min(u32(216), 4))],
        })
    return out


def load_api_stats() -> dict[int, dict]:
    """`Api.enc` → {角色代码: {字段: 值}}。**这是角色属性的正本**。"""
    data = decrypt("Api.enc")
    out = {}
    for i in range(len(data) // API_STRIDE):
        base = i * API_STRIDE
        out[i] = {
            name: int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
            for off, name in API_FIELDS.items()
        }
        out[i]["内禀抗性"] = [int.from_bytes(data[base + off:base + off + 4], 'little', signed=True)
                           for off in range(104, 144, 4)]
    return out


#: `Api.enc` 记录里的 **Big5 名称**在 `+12`。
#:
#: 找法：把 103 号记录逐字节试解 Big5，只有 `+12` 出得来「西夏兵」；
#: 交叉验证 1 号是「夏侯儀」、3 号是「封玲笙」，与我方名单一致。
#: （`+14` 会解出「夏兵」——那是从中间截断的，不是另一个字段。）
API_NAME_OFF = 12
API_NAME_LEN = 20


def load_enemies(groups: list[dict]) -> dict:
    """遇敌组点过名的敌人：名称 + 战斗要用的属性。

    **只导遇敌组引用到的那些**（139 个），不是 `Api.enc` 全部 1482 条 ——
    那里面绝大多数是没用上的空槽与我方角色，全导进去 JSON 会白胖一大圈。

    战斗素材的键按 `STN/ATT/HIT + 代码×10`（补足四位）推出：
    夏侯仪 1→`STN0010`、封铃笙 3→`STN0030`、西夏兵 103→`STN1030`。
    在遇敌组用到的 139 个代码上命中 135 个（97.1%），四个没命中的
    （207/245/246/248）本归档里确实没有对应素材。
    """
    data = decrypt("Api.enc")
    api = load_api_stats()
    codes = sorted({e["代码"] for g in groups for e in g["敌人"] if e["代码"]})
    out = {}
    for code in codes:
        base = code * API_STRIDE
        if base + API_STRIDE > len(data):
            continue
        raw = data[base + API_NAME_OFF:base + API_NAME_OFF + API_NAME_LEN]
        out[str(code)] = {
            "代码": code,
            "名称繁": _big5(raw),
            "素材": f"{code * 10:04d}",
            # 0x443e73读取Api+0x284：增援落位时避开大型敌人的完整占格。
            "占格类型": int.from_bytes(data[base + 0x284:base + 0x288], 'little'),
            # 官方Api装备槽 +0x90/+0x94/+0x98；原作0x42ba00等按此读取Ail2。
            "装备": {slot: f"{int.from_bytes(data[base + off:base + off + 4], 'little'):X}"
                     for slot, off in (("兵刃", 144), ("护甲", 148), ("饰物", 152))},
            **api.get(code, {}),
            **{key: (lambda value: value if value and value not in ("NULL", "DEFAULT") else None)(
                data[base + off:base + off + 10].split(b"\0")[0].decode("ascii").strip().upper())
                for off, key in ((718, "移动动作"), (728, "归位动作"), (738, "默认蓄劲动作"), (758, "默认施法动作"))},
            "绝学": [f"{int.from_bytes(data[base + 172 + i*4:base + 176 + i*4], 'little'):X}"
                     for i in range(min(36, int.from_bytes(data[base + 168:base + 172], 'little')))],
        }
    return out


def load_enemy_ai() -> dict:
    """官方0x444240/0x444330：Enemy_AI 56字节、MagicCon 64字节。"""
    import struct
    ai, magic = decrypt('Enemy_ai.enc'), decrypt('Magiccon.enc')
    if len(ai) % 56 or len(magic) % 64:
        raise ValueError('敌AI/绝学条件记录不完整')
    tactics = {}
    for off in range(0, len(ai), 56):
        row = struct.unpack_from('<14i', ai, off)
        tactics[str(row[0])] = dict(普攻=row[6], 防御=row[7], 绝学=row[8])
    conditions = {}
    for off in range(0, len(magic), 64):
        row = struct.unpack_from('<16i', magic, off)
        # 程序按记录下标查，不按首列；官方六条记录的首列互换，必须保留实际下标。
        conditions[f'{off // 64:X}'] = dict(原记录代码=row[0], 命上限百分数=row[6], 气上限百分数=row[7],
            队伍命条件原值=row[8], 掷值上限=row[9], 特殊选取=row[10],
            已有状态阵营=row[11], 已有状态=row[12], 缺少状态阵营=row[13], 缺少状态=row[14])
    return dict(行动概率=tactics, 绝学条件=conditions)


#: Api.enc 角色记录（与存档角色段同布局，见 assets/data/tsf_layout.json）：剩余五内、已学绝学数量与代码表。
API_REMAIN_OFF = 80
API_SKILL_COUNT_OFF = 168
API_SKILLS_OFF = 172
API_SKILLS_MAX = 64


def load_characters() -> dict:
    """我方七人的出场属性，**全部取自 `Api.enc`**（角色代码 = PARTY 序号 + 1）。

    2026-09-27 起不再读 300块 配套资料的 `data/characters.json`：那份初始绝学是 MOD 改过的
    （冰璃多「释剑之契」、葛云衣 29 门对官方 21 门、霍雍多「鬼缚之阵」，霍雍剩余五内 90 对官方 0）。
    绝学直接输出十六进制绝学代码（= Firttech 原记录代码），与存档、`battleAssets` 同一口径。
    """
    api = load_api_stats()
    raw = decrypt("Api.enc")
    i32 = lambda at: int.from_bytes(raw[at:at + 4], "little", signed=True)
    out = {}
    for index, name in enumerate(PARTY):
        code = index + 1
        base = code * API_STRIDE
        rec = {"代码": code, "序": index,
               "名称繁": _big5(raw[base + API_NAME_OFF:base + API_NAME_OFF + API_NAME_LEN])}
        rec.update(api.get(code, {}))
        rec["剩余五内"] = i32(base + API_REMAIN_OFF)
        # 官方0x41f750的DEFAULT分派：角色记录+718/+728/+758。
        # NULL要保留为无动作，不能再靠人物编号拼不存在的包。
        for off, key in ((718, "移动动作"), (728, "归位动作"), (758, "默认施法动作")):
            start = base + off
            value = raw[start:start + 10].split(b"\0")[0].decode("ascii").strip().upper()
            rec[key] = value if value and value not in ("NULL", "DEFAULT") else None
        count = max(0, min(i32(base + API_SKILL_COUNT_OFF), API_SKILLS_MAX))
        rec["绝学"] = [f"{i32(base + API_SKILLS_OFF + 4 * i):X}" for i in range(count)]
        out[name] = rec
    return out


def load_growth() -> dict:
    """五外成长率。**在 `4－角色资料.txt` 开头那张表里，不是 `【】` 记录**，
    所以 `parse_stats.py` 解不到，得单独抓。

    表里的数是**区间均值**，实际每级在 `[⌊均值⌋−1, ⌈均值⌉+1]` 内随机 —— 判据见
    `docs/专题/数值体系.md` §3.1：200 块「最大成长」存档三个等级完全线性，正是区间上界。
    """
    if SRC.名 == '官方':
        from pe_image import load
        import hashlib, struct
        import exe_tables
        pe = load(SRC.multimedia.parent / 'exe/RPG.exe')
        exe_tables.check(pe)
        read = lambda va: struct.unpack_from('<i', pe.data, pe.va_to_offset(va))[0]
        out = {}
        # 0x443d40：code*5+属性 -> 档次 -> 最小值/随机跨度。表与2in1未改成长版相同。
        for code, name in enumerate(PARTY, 1):
            ranges = {}
            for field, key in enumerate(('膂力', '体魄', '灵力', '迅捷', '机运')):
                if code > 6: ranges[key] = [0, 0]; continue
                index = read(0x46c1c4 + (code * 5 + field) * 4)
                lo, span = read(0x46c1b0 + index * 4), read(0x46c1c4 + index * 4)
                ranges[key] = [lo, lo + span]
            out[name] = dict(range=ranges, mean={k: sum(v)/2 for k,v in ranges.items()},
                             来源='官方exe成长表0x46c1b0；霍雍不增加五外')
        return out
    text = read_txt("*4－角色资料.txt")
    head = text[: text.index("第一部分") + 4000]
    out = {}
    for line in head.splitlines():
        parts = [p for p in re.split(r"[\s　]+", line.strip()) if p]
        if len(parts) != 6:
            continue
        name = NAME_FIX.get(parts[0], parts[0])
        nums = [as_num(p) for p in parts[1:]]
        if name not in PARTY or any(n is None for n in nums) or name in out:
            continue
        # ⚠️ 表头顺序是 膂力 体魄 灵力 迅捷 机运，**与 WUWAI 不同**，别直接 zip
        avg = dict(zip(("膂力", "体魄", "灵力", "迅捷", "机运"), nums))
        out[name] = {
            "mean": avg,
            "range": {k: [int(v // 1) - 1, int(-(-v // 1)) + 1] for k, v in avg.items()},
        }
    return out


def load_levelup() -> list[int]:
    """位阶 → 累计历练门槛。位阶 = 满足 `已有历练 ≥ 表[n]` 的最大 n。"""
    data = decrypt("Levelup.enc")
    return np.frombuffer(data[: len(data) // 4 * 4], dtype="<u4").astype(int).tolist()


#: 五内 → 八项抗性的系数（`innateResist`：内禀 = 100 + Σ 五内点数 × 系数），两个版本各一张。
#: **自有常量**（用户 2026-09-28 决定随代码发布）：数值最初取自 300块 配套资料《五魂化蕴》，
#: 「原版」已用官方版（200块版本）存档 8/8 验证、「本补丁」用 300块 存档 8/8 验证（见 docs/专题/数值体系.md §3.3）。
#: 在 RPG.exe 与全部 .enc 里按浮点/整数、行/列顺序都没搜到连续存放的表，推测是计算代码里的常数。
WUNEI_RESIST = {
    "原版": {
        "迅": {"焚火": -1.5, "雷荧": -1.5, "化相": -1},
        "烈": {"冰凛": -1, "外法": -0.75},
        "神": {"焚火": 1, "冰凛": 1.5, "雷荧": -1, "烨光": -2, "魔厉": 1.5, "化相": -0.5, "析魂": 1, "外法": 1},
        "魔": {"焚火": -1, "冰凛": -1.5, "雷荧": 1, "烨光": 1.5, "魔厉": -2, "化相": 1, "析魂": -0.5, "外法": -0.75},
        "魂": {"烨光": -0.5, "魔厉": -0.5, "析魂": -1},
    },
    "本补丁": {
        "迅": {"焚火": -0.5, "雷荧": -0.25, "化相": -0.5},
        "烈": {"冰凛": -0.5, "外法": -0.25},
        "神": {"焚火": 0.25, "冰凛": 0.25, "雷荧": -0.75, "烨光": -1, "魔厉": 0.75, "化相": -0.25, "析魂": 0.5, "外法": 0.5},
        "魔": {"焚火": -0.5, "冰凛": -0.5, "雷荧": 0.5, "烨光": 0.75, "魔厉": -1, "化相": 0.75, "析魂": -0.25, "外法": -0.25},
        "魂": {"烨光": -0.25, "魔厉": -0.25, "析魂": -0.25},
    },
}


def load_wunei_resist() -> dict:
    """五内抗性系数表（常量，见 WUNEI_RESIST），不再读配套资料。"""
    return {name: {w: dict(row) for w, row in table.items()} for name, table in WUNEI_RESIST.items()}


def load_skill_unlock() -> list[dict]:
    """绝学习得条件。`Magictb.enc` = 133 条 × 9 u32，字段布局见 §3.4。

    级别A＝**最低习得位阶**（需五内全部达标）；级别B＝**保底强制习得位阶**
    （**无视五内**；0 表示没有保底）。
    （夏侯仪「摄魂鬼爪」`[1,10,20,…]`：位阶 10 且魔20魂10 习得，否则位阶 20 强制学会）。
    """
    data = decrypt("Magictb.enc")
    rows = np.frombuffer(data[: len(data) // 4 * 4], dtype="<u4").reshape(-1, 9)
    out = []
    for r in rows:
        need = {k: int(v) for k, v in zip(WUNEI, r[3:8]) if v}
        out.append({"角色代码": int(r[0]), "级别A": int(r[1]), "级别B": int(r[2]),
                    "五内": need, "绝学代码": int(r[8])})
    return out


#: `Ail2.ENC` = **物品总表**，1882 条 × 926 字节，记录起始 20 字节是 **Big5 繁体名称**。
#: ⚠️ **记录下标 ＝ 物品编号（十六进制）** —— 判据：護身匕首 编号 1 落记录 1、
#: 凜日神刀 编号 `11`(=17) 落记录 17、布袍 编号 `65`(=101) 落记录 101；
#: 全表按此 join，215 件装备里 204 件与简体名**字数一致**。
#:
#: 为什么非要它：**字库是繁体（Big5 码位），而配套资料导出的名称是简体**。
#: 「护」「仪」「国」都不在 `font24` 里，直接拿简体名去画会缺字。
#: 所以给每条物品补一个 `名称繁`，界面用它画。见 `docs/专题/数值体系.md`。
AIL2_STRIDE = 926
AIL2_NAME_LEN = 20


def load_item_names() -> list[str]:
    """`Ail2.ENC` 每条记录开头的 Big5 繁体名称，下标即物品编号。"""
    data = decrypt("Ail2.ENC")
    out = []
    for i in range(len(data) // AIL2_STRIDE):
        blob = data[i * AIL2_STRIDE: i * AIL2_STRIDE + AIL2_NAME_LEN].split(b"\x00")[0]
        try:
            out.append(blob.decode("big5"))
        except UnicodeDecodeError:
            out.append("")
    return out


#: `Magiccon.enc` 的记录布局。**下标即绝学代码**（十六进制），名称是 Big5 繁体。
#:
#: 判据：`(19268−4)/64 = 301` 而 `skills.json` 里「摄魂鬼爪」的绝学代码正是 301；
#: 代码 191₁₆=401 取出「氣癒之術」，与 MOD 工具界面显示的完全一致。
#: 528 处 Big5 串的相邻间距**全部是 64**，记录长度没有悬念。
MAGICCON_STRIDE = 64
MAGICCON_NAME_OFF = 4


def load_skill_names() -> list[str]:
    """`Magiccon.enc` 每条记录 +4 处的 Big5 繁体绝学名，下标即绝学代码。"""
    data = decrypt("Magiccon.enc")
    out = []
    for i in range(len(data) // MAGICCON_STRIDE):
        head = i * MAGICCON_STRIDE + MAGICCON_NAME_OFF
        blob = data[head:head + 20].split(b"\x00")[0]
        try:
            out.append(blob.decode("big5"))
        except UnicodeDecodeError:
            out.append("")
    return out


#: 资料里给同名绝学加的区分后缀：「无极天光3」「漫天箭雨2」。
_SUFFIX_NUM = re.compile(r"\d+$")
#: enc 里给同名绝学加的角色标注：「紫琰飛煌(慕)」「雙劍合璧（雄）」。
_PAREN = re.compile(r"[（(][^）)]*[）)]\s*$")


def _strip_tags(text: str) -> str:
    """反复剥掉结尾的括号标注与序号，直到不再变化。

    ⚠️ **必须循环**：「无极天光3（武英仲）」先去数字没用（结尾是括号），
    去完括号又剩个 3。只做一遍会漏掉这一类，89 条绝学因此配不上名字。
    多层括号（「混元气聚（古伦德）（…套装技）」）也靠这个循环剥干净。
    """
    out = str(text or "").strip()
    while True:
        nxt = _SUFFIX_NUM.sub("", _PAREN.sub("", out).strip()).strip()
        if nxt == out:
            return out
        out = nxt


#: `Firttech.enc` 的记录布局：MOD 1482条、官方500条，每条560字节；官方按下标寻址，
#: 说明文字（Big5 繁体）在 +380。
#:
#: 判据：气愈之术(401) 的说明落在 224940、雷引之术(418) 落在 234460，
#: 相差 9520 而代码相差 17 —— 9520/17 = 560 精确整除；再回代
#: `418×560 + 380 = 234460` 分毫不差。829920/560 = 1482 也整除。
#: 取出的文字与 MOD 工具界面显示的逐字相同。
#:
#: ⚠️ 曾经推断「说明在别处，因为 Magiccon 一条只有 64 B 放不下」——
#: 方向对了但**没验证就当结论说**。正确做法是拿截图上的原文去所有 enc 里搜，
#: 一次就命中。
FIRTTECH_STRIDE = 560
FIRTTECH_DESC_OFF = 380


#: `Firttech.enc` 里**已验证**的字段偏移。字段名与枚举取自 MOD 工具 exe 的
#: 字段规格区（UTF-16LE，0x3ED000–0x3F9000）。
#:
#: ⚠️ **每一条都是拿 454 条绝学做全表对齐验出来的，不是单条猜的。**
#: 单条对照极易自欺 —— 气愈之术的「消耗元气」和「咒术相性 A」都等于 10，
#: 只看那一条会把 +56 认成相性。
#:
#: 没验出来的字段一律不导（绝技类型、绝学指数、附加作用量、特效一~五…），
#: 宁可少给也不要给错。
FIRTTECH_FIELDS = {
    # 偏移: (字段名, 单位换算, 备注)
    24: ("绝学类型码", 1, None),       # 0普攻 1绝技 2咒法 4阵法 5蛰伏 6绝对防御
    28: ("作用场合", 1, None),         # USAGE_CONTEXT
    32: ("作用对象码", 1, None),       # TARGET_CAMP
    36: ("作用范围码", 1, None),       # TARGET_SCOPE
    40: ("前后排判定码", 1, None),     # TARGET_ROW
    148: ("攻击位置码", 1, None),      # ATTACK_POS ⚠️ 订正：此前误记为 +44
    48: ("绝技类型", 1, None),         # 一~七型，决定伤害公式
    52: ("消耗体力值", 1, None),
    56: ("消耗元气值", 1, None),
    60: ("咒术相性码", 1, None),       # SKILL_ELEMENT
    64: ("绝学指数", 1, None),         # 伤害指数 / 回复主作用量
    68: ("附加作用量类型", 1, None),   # EXTRA_KIND
    72: ("附加作用命中率", 1, None),
    76: ("附加作用量", 1, None),       # 咒术的固定伤害就是它
    80: ("伤害缩减指数", 1, None),     # exe 的 r
    84: ("命作用百分数", 1, None),
    88: ("气作用百分数", 1, None),
    92: ("吸命百分数", 1, None),
    96: ("吸气百分数", 1, None),
    320: ("动画一图层高度", 1, None),
    324: ("动画二图层高度", 1, None),
    340: ("动画一位置码", 1, None),
    360: ("动画一目标码", 1, None),
}

#: 「300块3.0版专用」那组是**单字节**，不是 int32。
#: ⚠️ 拿 int32 去扫必然扫不到 —— 我为此白找过一轮。
FIRTTECH_BYTE_FIELDS = {
    485: ("消耗命气形式", {0: "点数形式", 1: "百分比形式"}),
    486: ("限制使用次数", {0: "无限制", 1: "限用一次"}),
    487: ("绝学特殊效果", {0: "无", 1: "神氛化法", 2: "无上灭法", 3: "神视幻观",
                           4: "神仪逆阵", 5: "玄枢逆阵", 6: "时仪返召",
                           7: "乱神大法", 8: "逆时之契"}),
    488: ("数值显示", {0: "无显示", 1: "有显示"}),
}

#: exe 界面上有、但**不在这 560 字节里**的字段（三条记录交叉验证全部落空）。
#: 发动命气条件/权重现已从MagicCon解出（load_enemy_ai）；其余特殊能力仍需独立核对。
NOT_IN_FIRTTECH = ("自爆概率", "一击致命概率", "死人时可用", "绝学重击率",
                   "可召唤小弟种类", "召唤数量", "召唤阵营", "是否变身",
                   "体力值条件", "元气值条件", "全队平均体力值条件", "概率权重")

#: 特效**五格 + 五个概率**，两组各自连排。
FIRTTECH_EFFECT_OFFS = (120, 124, 128, 132, 136)
FIRTTECH_EFFECT_PROB_OFFS = (100, 104, 108, 112, 116)

TARGET_CAMP = {0: "敌方阵营", 1: "己方阵营", 2: "双方阵营", 3: "死者", 4: "自体"}
TARGET_SCOPE = {0: "单体", 1: "直列", 2: "横排", 3: "全体"}
TARGET_ROW = {0: "仅前排", 1: "无限制"}
#: 攻击位置。抄自 exe 0x3ef2a8。⚠️ 此前只写了 4 项且 2/3 都错了。
ATTACK_POS = {0: "原地", 1: "目标身上", 2: "目标前面一行", 3: "目标前面二行",
              4: "目标前面三行", 5: "目标阵前", 6: "屏幕中间"}
ANIM_POS = {0: "全屏动画专用", 2: "针对目标单体", 3: "针对一横排", 4: "针对目标阵营"}
ANIM_TARGET = {0: "落在一个目标身上", 1: "落在目标阵营所有角色身上"}

#: 附加作用量的形式。exe 原文：0=伤害/回复**点数**（受灵力等影响）；
#: 1=**命极百分数**（不受灵力影响）；3=点数但**固定值、特效必中**（状态/玄经/阵法
#: 都是这种）；2=同 3，唯一区别是 2 可用于平时治疗、3 不行。
EXTRA_KIND = {0: "点数", 1: "命极百分数", 2: "固定点数(平时可用)", 3: "固定点数"}

#: `绝学类型`（`Firttech.enc` +24）。枚举名逐字抄自 MOD 工具 exe 的字段规格区。
#:
#: ⚠️ **菜单绝学页只有两格「絕技 / 咒法」**，而这里有六种。归格规则在前端的
#: `skillbook.SKILL_KINDS`：阵法归咒法格，普攻/蛰伏/绝对防御不是玩家能选的绝学，
#: 一格都不进。官方版 171 门的分布是 绝技93 / 咒法65 / 阵法7 / 普攻4 / 蛰伏1 / 绝对防御1。
SKILL_KIND = {0: "普攻", 1: "绝技", 2: "咒法", 4: "阵法", 5: "蛰伏", 6: "绝对防御"}

#: 咒术相性。**逐条抄自 exe 规格区 0x3eecc0，码是十六进制。**
#: ⚠️ 推翻旧结论：此前我按八抗性顺序外推出 `6化相 / 7析魂 / 8外法`，
#: **exe 里根本没有这三个值** —— 只有 0~5 与 A(10)、B(11)。那是我编的。
SKILL_ELEMENT = {0: "其它咒法", 1: "火系咒法", 2: "冰系咒法", 3: "雷系咒法",
                 4: "光系咒法", 5: "暗系咒法", 10: "恢复系咒法", 11: "辅助系咒法"}

#: 相性 → 及身抗性的列名。咒术伤害要乘目标的这一项抗性（配套资料【咒术伤害】段）。
ELEMENT_RESIST = {1: "焚火", 2: "冰凛", 3: "雷荧", 4: "烨光", 5: "魔厉"}

#: 附加特效（状态）代码。**逐条抄自 exe 规格区 0x3efda4~0x3efeb8，码是十六进制。**
#:
#: ⚠️ **`0` 是「中毒」，不是「没有特效」。** 判断某一格有没有特效要看
#: **特效概率**（概率为 0 才是空格）—— 拿码去判会把全部中毒特效丢掉。
#: ⚠️ 推翻旧结论（此前靠 txt 状态名反解，错了两处）：
#:   `9` 是**冻结**不是「冰结」；`19` 是**替身**不是「替护」。
#:   `26 迅目`、`27 复活` 此前完全没有 —— 反解时没样本。
SKILL_EFFECT = {
    0: "中毒",   1: "麻痹",   2: "封咒",   3: "疲弱",   4: "迟缓",  5: "愚钝",
    6: "蚀御",   7: "障眼",   8: "灼伤",   9: "冻结",  10: "慎惧", 11: "逆阙",
    13: "伥魂", 14: "幻影",  15: "狂暴",  16: "操偶",  19: "替身",
    21: "命蕴", 22: "神力",  23: "奋驰",  24: "圣睿",  25: "披甲",
    26: "迅目", 27: "复活",
}

USAGE_CONTEXT = {0: "仅平时", 1: "仅战斗时", 2: "无限制"}

#: `消耗元气值` 是百分数还是点数的开关。**exe 里这个字段叫「消耗命气形式」**，
#: 位置 +485 与当初统计扫出来的一致 —— 这次是拿引召狮鹫那一屏确认的（它是
#: 「1:百分比形式」，灵魂震爆是「0:点数形式」）。
QI_COST_IS_PERCENT_OFF = 485

#: 蓄劲/回气存的是 **秒 × 20000**。（MOD 工具界面显示的是毫秒，正好是 /20。）
FIRTTECH_TIME = {140: "蓄劲秒", 144: "回气秒"}

#: 绝学的**五个动作**与动画文件，都是记录里的 ASCII 串。
#:
#: ⚠️ **偏移直接抄自 exe 规格区**（`tools/exe_field_spec.py` 第 231~237 行，
#: 字段名也是 exe 原话）—— 不是猜的。此前一直没导，于是战斗里
#: **绝学没有出招姿势、蓄劲期间人站着不动**，用户 2026-09-18 报的
#: 「防御是有防御姿势的好吗？蓄力也有对应的姿势」。
#:
#: 值是素材包名（例如ATT0010）；DEFAULT查人物默认动作，NULL不制造替代动作。
#: 兼容字段只保存明确包名，另用“原值”字段保留这一区别。
FIRTTECH_ACTIONS = {
    156: "蓄劲动作",
    166: "移动动作",
    176: "攻击动作",
    186: "失误动作",
    196: "归位动作",
    250: "动画文件一",
}

#: 动作与五个动画文件槽均为10字节；不能读16字节越过相邻槽。
FIRTTECH_ACTION_LEN = 10
FIRTTECH_ANIM_LEN = 10
FIRTTECH_TICKS_PER_SEC = 20000


def animation_slots(data: bytes, base: int, names_off: int,
                    controls_off: int, heights_off: int,
                    positions_off: int, targets_off: int) -> list[dict]:
    """同构的五槽记录；空槽保留编号，帧事件的3不能因槽2为空而变成2。

    Firttech: 250/300/320/340/360；Ail2: 716/766/786/806/826。
    文件名前24字节为槽上限、前20字节为五个触发号。
    官方RPG.exe 0x444b4a按触发号匹配帧事件，不能假定触发号等于槽号。
    控制码是高度基准（0固定、1施术者、2目标），保留原字段兼容。
    """
    slots = []
    for i in range(5):
        off = base + names_off + i * 10
        raw = data[off:off + 10].split(b"\0")[0].decode("ascii").strip()
        values = [int.from_bytes(data[base + p + i * 4:base + p + i * 4 + 4],
                                 "little", signed=True)
                  for p in (controls_off, heights_off, positions_off, targets_off)]
        control, height, position, target = values
        slots.append({"槽号": i + 1, "原始文件": raw,
                      "文件": raw.upper() if raw and raw.upper() not in ("NULL", "DEFAULT") else None,
                      "触发号": int.from_bytes(data[base + names_off - 20 + i * 4:
                                                    base + names_off - 16 + i * 4], "little", signed=True),
                      "控制码": control, "图层高度": height,
                      "位置码": position, "目标码": target,
                      "位置": ANIM_POS.get(position, position),
                      "目标": ANIM_TARGET.get(target, target)})
    return slots


def firttech_offsets(data: bytes) -> dict[int, int]:
    """运行索引→记录偏移。官方0x439f5a原样加载，0x422f32按索引×560寻址。

    官方305/306首列互换，但原程序不重排；首列保留为原记录代码。
    MOD尚未追踪其EXE寻址，暂保留既有首列键，不外推官方结论。
    """
    return {(index if SRC.名 == '官方' else int.from_bytes(data[index*FIRTTECH_STRIDE:index*FIRTTECH_STRIDE+4], 'little')):
            index*FIRTTECH_STRIDE for index in range(len(data)//FIRTTECH_STRIDE)}


def attach_skill_fields(rows: list[dict], data: bytes) -> int:
    """把 `Firttech.enc` 里已验证的字段补到绝学记录上。"""
    offsets = firttech_offsets(data)
    hit = 0
    for row in rows:
        code = half(str(row.get("绝学代码", "")))
        if not re.fullmatch(r"[0-9A-Fa-f]+", code):
            continue
        base = offsets.get(int(code, 16))
        if base is None:
            continue
        row["原记录代码"] = int.from_bytes(data[base:base + 4], "little")
        for off, (name, _, _) in FIRTTECH_FIELDS.items():
            row[name] = int.from_bytes(data[base + off:base + off + 4], "little")
        for off, name in FIRTTECH_TIME.items():
            ticks = int.from_bytes(data[base + off:base + off + 4], "little")
            row[name] = round(ticks / FIRTTECH_TICKS_PER_SEC, 3)
        row["动画槽"] = animation_slots(data, base, 250, 300, 320, 340, 360)
        row["动画槽上限"] = int.from_bytes(data[base + 226:base + 230], "little", signed=True)
        for off, key in FIRTTECH_ACTIONS.items():
            if off >= 206:
                continue
            row[key + "原值"] = data[base + off:base + off + 10].split(b"\0")[0].decode("ascii").strip().upper()
        # 五个动作 + 动画文件（ASCII 串，`\0` 截断）。空串就不写这个键，
        # 免得前端拿到 "" 当成"有动作但名字是空的"。
        for off, name in FIRTTECH_ACTIONS.items():
            span = FIRTTECH_ANIM_LEN if name == "动画文件一" else FIRTTECH_ACTION_LEN
            raw = data[base + off:base + off + span].split(b"\x00")[0]
            text = raw.decode("ascii", "ignore").strip().upper()
            # ⚠️ **`NULL` 与 `DEFAULT` 都不是包名**：
            #   * `NULL`    ＝ 这一门没有这个动作；
            #   * `DEFAULT` ＝ **用这个角色自己的默认动作**（摄魂鬼爪有专属的
            #     `RED0011`，天霜雪舞写 `DEFAULT` → 用夏侯仪的 `RED0010`）。
            # 两个都不写进 json，前端见到缺字段就回落到角色默认包。
            # 让它们进 json 的后果是前端拿 `NULL`/`DEFAULT` 去请求素材，404 一片。
            if text and text not in ("NULL", "DEFAULT"):
                row[name] = text
        row["能否平时使用"] = USAGE_CONTEXT.get(row["作用场合"], "未知")
        # 码 → 名。查不到的**保留码值**，不要写「未知」把信息抹掉。
        for src, dst, table in (("绝学类型码", "绝学类型", SKILL_KIND),
                                ("作用对象码", "作用对象", TARGET_CAMP),
                                ("作用范围码", "作用范围格", TARGET_SCOPE),
                                ("前后排判定码", "前后排判定", TARGET_ROW),
                                ("攻击位置码", "攻击位置", ATTACK_POS),
                                ("附加作用量类型", "附加作用量形式", EXTRA_KIND),
                                ("动画一位置码", "动画一位置", ANIM_POS),
                                ("动画一目标码", "动画一目标", ANIM_TARGET),
                                ("咒术相性码", "咒术相性", SKILL_ELEMENT)):
            row[dst] = table.get(row[src], row[src])
        # 「300块3.0版专用」那组是单字节。
        for off, (fname, table) in FIRTTECH_BYTE_FIELDS.items():
            code = data[base + off]
            row[fname] = table.get(code, code)
        # 附加特效：**码在一组、概率在另一组**，两组按格号配对。
        # ⚠️ **空格判据是「概率为 0」，不是「码为 0」** —— 码 0 是中毒。
        eff = []
        for o_code, o_prob in zip(FIRTTECH_EFFECT_OFFS, FIRTTECH_EFFECT_PROB_OFFS):
            code = int.from_bytes(data[base + o_code:base + o_code + 4], "little")
            if not int.from_bytes(data[base + o_prob:base + o_prob + 4], "little"):
                continue
            eff.append({
                "码": code,
                "名称": SKILL_EFFECT.get(code, code),
                "概率": int.from_bytes(data[base + o_prob:base + o_prob + 4], "little"),
            })
        row["附加特效"] = eff
        row["附加特效名"] = [e["名称"] for e in eff]
        row["元气消耗按百分比"] = bool(data[base + QI_COST_IS_PERCENT_OFF])
        hit += 1
    return hit


def load_skill_descs() -> list[str]:
    """`Firttech.enc` 每条记录 +380 的 Big5 繁体绝学说明，下标即绝学代码。"""
    data = decrypt("Firttech.enc")
    out = []
    for i in range(len(data) // FIRTTECH_STRIDE):
        head = i * FIRTTECH_STRIDE + FIRTTECH_DESC_OFF
        blob = data[head:head + 180].split(b"\x00")[0]
        try:
            out.append(blob.decode("big5").strip())
        except UnicodeDecodeError:
            out.append("")
    return out


def attach_skill_desc(rows: list[dict], descs: list[str]) -> int:
    """按绝学代码补 `说明繁`。只有我方绝学有说明，敌人绝学大多为空。"""
    hit = 0
    for row in rows:
        code = half(str(row.get("绝学代码", "")))
        if not re.fullmatch(r"[0-9A-Fa-f]+", code):
            continue
        index = int(code, 16)
        text = descs[index] if 0 <= index < len(descs) else ""
        if text:
            row["说明繁"] = text
            hit += 1
    return hit


def attach_skill_traditional(rows: list[dict], names: list[str]) -> int:
    """按绝学代码补 `名称繁`。两边**各自去掉区分后缀**再比字数。

    字数对不上的不采用 —— 与 `attach_traditional` 同样的保守策略：
    宁可留空让界面退回简体（可能缺字），也不要贴错名字。
    """
    hit = 0
    for row in rows:
        code = half(str(row.get("绝学代码", "")))
        if not re.fullmatch(r"[0-9A-Fa-f]+", code):
            continue
        index = int(code, 16)
        raw = names[index] if 0 <= index < len(names) else ""
        if not raw:
            continue
        trad, plain = _strip_tags(raw), _strip_tags(row.get("名称", ""))
        if trad and len(trad) == len(plain):
            row["名称繁"] = trad
            hit += 1
    return hit


#: `Ail2.ENC` 记录里**繁体说明文字**的偏移。
#:
#: 一条记录 926 字节：名称在 +0（20 B），说明在 +392。
#: 判据：编号 61（铁枪）取出「鐵簇木柄的短槍，輕便易使，是騎兵所愛用的兵刃。」，
#: 与原作法宝页截图**逐字相同**。1882 条记录里 368 条有说明。
AIL2_DESC_OFF = 392
AIL2_DESC_LEN = 200


def load_item_descs() -> list[str]:
    """`Ail2.ENC` 每条记录 +392 的 Big5 繁体说明，下标即物品编号。"""
    data = decrypt("Ail2.ENC")
    out = []
    for i in range(len(data) // AIL2_STRIDE):
        head = i * AIL2_STRIDE + AIL2_DESC_OFF
        blob = data[head:head + AIL2_DESC_LEN].split(b"\x00")[0]
        try:
            out.append(blob.decode("big5").strip())
        except UnicodeDecodeError:
            out.append("")
    return out


#: `Ail2.ENC` 里**已验证**的装备/物品数值字段（相对记录起点的字节偏移）。
#:
#: ⚠️ 全部用 214 件装备做**全表对齐**验出，吻合率见括号。三条注意：
#:   1. **按有符号 int32 读** —— 补正有负值（幽日神袍闪避 −10），
#:      按无符号读会变成 40 亿，对齐率直接掉到零。
#:   2. **空白的语义两样**：补正类空白 = 0，**抗性类空白 = 100**
#:      （配套 xlsx「抗性空白值为１００」说的是默认**乘数** 1.0）。
#:      拿 0 去比抗性，吻合率只有 50%。
#:   3. 八抗性连续排在 +120…+148，步进 4 —— 结构自洽本身就是佐证。
#: `Ail2.ENC` 里用器的**使用规则与效果参数**。
#:
#: 字段名与枚举来自 MOD 工具 exe 的字段规格区；偏移用工具界面上**大補丸(FC)**
#: 那一屏逐项校准 —— 整组连续吻合，其中 `+212 = 10 = 0xA` 正好是界面显示的
#: 「A:恢复」相性，这一条基本排除了巧合。
#:
#: ⚠️ **曾经错过一次**：拿配套 txt 的「使用时机」（皆可/战斗/平时，三值）去对
#: enc 的「作用场合」（不可用/仅战斗时/仅平时/无限制，**四值**），语义与编码
#: 都不一样，怎么扫都扫不出来，于是错误地宣布"游戏里没有这个字段"。
#: **正确做法是先从 exe 读出字段规格，再拿工具界面上的一条记录校准偏移** ——
#: 不要让配套 txt 决定你能找到什么。
AIL2_RULES = {
    # ⚠️ **`物品类别` 是原始字段，2026-08-30 标定** —— 拿護身匕首(1武器)/布袍(2防具)/
    # 辟邪玉佩(3饰物)/金創藥(0用器) 四条交叉匹配，全表 926 字节里 **只有 +24 同时命中**。
    # 它**取代了原先按物品编号分段推断槽位**的那套（见 `equip_slot` 的说明）——
    # 那套在官方版全表上是错的：类别为武器的有 224 件，远超「编号 ≤100」那一段。
    24: ("物品类别", {0: "用器", 1: "武器", 2: "防具", 3: "饰物", 4: "杂类"}),
    # `武器攻击范围`，同日标定。判据是语义完全吻合：索带(41-58 封铃笙)=前排一横排、
    # 枪戟(61-78 古伦德/萧熇)=一直列、法珠(81-98 慕容璇玑/葛云衣)=任意单体、
    # 刀剑(1-40)=前排单体。**仅当物品类别为武器时才有意义**（exe 原话）。
    52: ("武器攻击范围", {0: "前排单体", 1: "任意单体", 2: "一直列",
                          3: "前排一横排", 4: "全体"}),
    172: ("作用场合", {0: "不可用", 1: "仅战斗时", 2: "仅平时", 3: "无限制"}),
    # ⚠️ **2026-09-18 订正：`作用对象` 原先写的是 +176，错的。**
    #
    # `+176` 在**我方 48/49 是 1、敌方 11/13 也是 1** —— 毫无区分度，
    # 于是雷火弹（攻击道具）解出来是「己方阵营」，全表除 2 条外所有可用的
    # 用器都是己方阵营。它当初**只用一条记录（大補丸）校准过**，
    # 违反判据表第一条「一条记录不够，要三条交叉」。
    #
    # 重标办法是**全表交叉**：配套资料「2－用器资料」97 条里有 62 条标了
    # 【使用对象】（49 我方 / 13 敌方），拿它们扫 926 字节里每一个偏移。
    # `+184` **命中 57/62**，第二名只有 44 —— 区分度断层领先。
    #
    # 剩下 5 条对不上的，逐条查清都不是反例：
    #   * 還神散 / 紫凝甘露 / 鑄命封石 三件 `+184 = 3`，详述全是
    #     「**使昏迷者苏醒**」—— 复活类。配套资料只有「我方/敌方」两档，
    #     **引擎多一档「死者」**，所以是枚举更细，不是解错；
    #   * 逆血邪丹 `作用场合 = 不可用`，本来就不参与；
    #   * 迅神五書 `作用场合 = 仅平时`，是 MOD 改过数值的那一类
    #     （我们读的是官方版 enc）。
    #
    # ⚠️ **`+184` 此前被试过、被否掉**，理由是「出现枚举里没有的值 3」——
    # 那次是拿它当**作用范围**试的，3 确实不在作用范围的枚举里；
    # 当**作用对象**用，3 正好是「死者」。**否掉一个偏移之前要把
    # 每一种字段解释都试一遍**，别因为配错了枚举就把偏移一起判死。
    184: ("作用对象", {0: "敌方阵营", 1: "己方阵营", 2: "双方阵营", 3: "死者", 4: "自体"}),
    # ⚠️ **2026-09-05 订正：作用范围原先写的是 +180 / +184，两个都错。**
    #   * `+180` 全表 700 条**恒为 0** —— 于是 141 件物品的作用范围全成了「单体」，
    #     百草沁香那批「全体回复」被当成单体。**分布只有一个值本身就该报警。**
    #   * `+184` 出现枚举里没有的值 3。
    # 判据是配套资料「2－用器资料」97 条的【使用范围】做**全表交叉**：
    #   +188：全体 18/19、前后排单体 37/38、一横排 2/2   ← 唯一有区分度的
    #   +192：前后排单体 37/38 = 1(无限制)
    # 对不上的几条正是 MOD 版改过数值的（我们读的是官方版 enc）。
    188: ("作用范围", {0: "单体", 1: "直列", 2: "横排", 3: "全体"}),
    192: ("前后排判定", {0: "仅前排", 1: "无限制"}),
}

#: 用器的**使用特效**（2026-09-05 标定）。结构与 `Firttech` 的特效一~五完全同构。
#:
#: exe 规格区 `0x3f2bcc` 起就把这十个字段名逐个列着（使用特效一~五 / 一~五概率），
#: 紧跟一段**用器专属**的枚举 `0x3f2c58`（⚠️ **码是十六进制**）：
#:     0x36 增加膂力 / 0x37 增加体魄 / 0x38 增加迅捷 / 0x39 增加机运
#:     0x3A 增加灵力 / 0x3B 增加命极 / 0x3C 增加气极
#: 低位码（0~0x1B）与绝学表的「特效」共用同一张表（中毒/麻痹/…/复活）。
#:
#: **三条独立判据，全中：**
#:   1. 三魄烈丹 特效 = [22,23,25,26] = 神力/奋驰/披甲/迅目 —— 说明文字写的是
#:      「服食後同時產生**神力、奮馳、披甲、迅目**等效」，四项逐字逐序一致。
#:   2. 八络血参 = 增加命极，附加作用量 20 ↔ txt「增加**２０**点体力上限」。
#:   3. 熊王金胆 = 增加膂力，附加作用量 5  ↔ txt「增加**５**点膂力」。
#:
#: 这同时解掉一个悬案：那五件属性药在**别的字段上一模一样**（全 926 字节逐字节
#: diff 过，除名称/编号/说明外只有 +252 一处不同），加哪一项属性原本无从判断。
AIL2_EFFECT_PROB = (232, 236, 240, 244, 248)
AIL2_EFFECT_CODE = (252, 256, 260, 264, 268)

#: 用器特效码 → 含义。0~0x1B 段与绝学共用（这里只列平时用得上的）；
#: 0x36~0x3C 抄自 exe 规格区 `0x3f2c58`。
#:
#: ⚠️ **61 / 63 / 64 三个码 exe 规格区查无此码**，语义是拿配套 txt 的【详述】
#: 唯一定下来的（地返遁符「产生"移行化法"效果」/ 镇辟玄香「降低遇敌率」/
#: 鬼魅玄香「大幅提升遇敌率」）。已登记进 `docs/状态/复现度台账.md`。
#: 状态药物那批解除类的码（40/41/42/51/67）同样查无此码，**且全是仅战斗时**，
#: 这一轮不需要，故不在表内 —— 导出时原样保留数字。
ITEM_EFFECTS = {
    54: "增加膂力", 55: "增加体魄", 56: "增加迅捷", 57: "增加机运",
    58: "增加灵力", 59: "增加命极", 60: "增加气极",
    61: "移形化法", 63: "降低遇敌率", 64: "提升遇敌率",
}

#: 「增加XX」特效加的是角色身上的哪个键。键名与 `partyState` 的成员字段一致。
#: ⚠️ 膂力/体魄/迅捷/机运/灵力 是**五外**的五项，命极/气极是上限。
EFFECT_TO_FIELD = {
    "增加膂力": "膂力", "增加体魄": "体魄", "增加迅捷": "迅捷",
    "增加机运": "机运", "增加灵力": "灵力",
    "增加命极": "命极", "增加气极": "气极",
}

#: 用器的使用动画五槽从 `Ail2.ENC +716` 起，每槽10字节。
#:
#: 判据：金创药 = `EFF3001`、百草沁香 = `EFF3002`，而 `Firttech` 里
#: 氣癒之術(191) 的动画一也是 `EFF3001`、神氣流轉(192) 是 `EFF3002` ——
#: **单体恢复与全体恢复各自共用一个动画**，这正好解释了原作截图里
#: 「用金创药」和「放气愈之术」的光效长得一模一样。
AIL2_USE_ANIM_OFF = 716
AIL2_USE_ANIM_LEN = 10

#: `炼化类型`（`Ail2.ENC` **单字节 +882**，2026-08-30 标定）。
#:
#: **这就是「夏侯仪不能用长枪」那条限制的真身。** 判据是按编号分段扫出来的唯一命中：
#: 匕首段(1–18)/长剑段(21–40)/索带段(41–58)/枪戟段(61–78)/法珠段(81–98)/法袍段(101–129)
#: 六段各自常量且互不相同，全表 926 个字节位置里**只有 +882 满足**；
#: 再拿 300块 的 215 件装备与配套 txt 的「可装备者」交叉，
#: 匕首→夏霍 18/18、长枪→古萧 18/18、索带→封高 18/18、宿玉→璇葛 18/18。
#:
#: ⚠️ **是单字节，不是 int32** —— 拿 int32 扫必然扫不到（同 `Firttech` 的 +485~488）。
AIL2_REFINE_OFF = 882
AIL2_REFINE = {
    0: "杂类", 1: "匕首", 2: "长剑", 3: "长枪", 4: "索带", 5: "宿玉",
    6: "法袍", 7: "轻甲", 8: "重甲", 9: "男饰", 10: "女饰", 11: "通饰",
    12: "符器", 13: "不明", 14: "回复药物", 15: "属性药物", 16: "状态药物", 17: "奇石",
}

#: **炼化类型 → 能装备它的角色**。判红就用这张表。
#:
#: 兵刃五类是**硬结论**：300块 215 件装备与配套 txt 的「可装备者」逐件交叉，
#: 四类 18/18 全中；官方版还能被开局装备二次验证
#: （冰璃开局「长剑」、封铃笙开局「白绢索带」）。
#:
#: ⚠️ **护甲与饰物是近似**。原作那边**逐件不同** —— 光「法袍」在 300块 txt 里
#: 就有六种不同的可装备者组合。真值在 `Ail2.ENC` 的 `可装备者` 字段里
#: （exe 规格区 `0x3f25d0`：「指定可装备该装备的人物代码」），**该字段尚未标定**：
#: int32 与单字节两轮全表扫描都没命中。这里先**按类型取并集**顶上，
#: 宁可放宽也不误判红。定位到那个字段后，这张表就该整个删掉。
EQUIP_BY_REFINE = {
    "匕首": ["夏侯仪", "霍雍"],
    "长剑": ["冰璃"],
    "长枪": ["古伦德", "萧熇"],
    "索带": ["封铃笙", "高皇君"],
    "宿玉": ["慕容璇玑", "葛云衣"],
    "法袍": None,                                          # None = 所有人
    "轻甲": ["冰璃", "古伦德", "武英仲", "萧熇"],
    "重甲": ["古伦德", "萧熇"],
    "男饰": ["夏侯仪", "古伦德", "霍雍", "武英仲", "萧熇"],
    "女饰": ["冰璃", "封铃笙", "慕容璇玑", "葛云衣", "高皇君"],
    "通饰": None,
}


#: 用器的效果数值（无枚举，直接取整数）。
AIL2_EFFECT = {
    196: "主作用量", 200: "附加作用量类型", 204: "命中率", 208: "附加作用量",
    212: "附加作用量相性", 216: "命作用百分数", 220: "气作用百分数",
    224: "吸命百分数", 228: "吸气百分数",
}

AIL2_STATS = {
    20: "商店售价",      # 98.8%
    36: "物品等级",      # 99.4%
    44: "命极补正",      # 99.5%
    48: "气极补正",      # 99.5%
    56: "攻击补正",      # 99.5%
    60: "防御补正",      # 99.5%
    64: "命中补正",      # 99.5%
    68: "闪避补正",      # 99.5%
    72: "法力补正",      # 98.6%
    76: "必杀补正",      # 99.5%
    120: "焚火", 124: "冰凛", 128: "雷荧", 132: "烨光",     # 98.6~100%
    136: "魔厉", 140: "化相", 144: "析魂", 148: "外法",
}


def attach_stats(rows: list[dict], key: str, data: bytes,
                 names: list[str] | None = None) -> int:
    """用 `Ail2.ENC` 的数值**覆盖**配套 txt 的 —— 摆脱 txt 的那一步。

    enc 是当前安装版本的真值；txt 是某个版本导出的快照，换版本就不对了。
    两边不一致时把 txt 的值留在 `<字段>·txt`，方便回头查差异。
    """
    n = len(data) // AIL2_STRIDE
    hit = 0
    by_name = {}
    if names:
        for i, nm in enumerate(names):
            by_name.setdefault(nm.strip(), i)
    for row in rows:
        code = half(str(row.get(key, "")))
        # ⚠️ 少数条目代码格填的不是十六进制（聚元散那格写的是「买炼」），
        # 退回按名称反查 —— 与 `attach_desc` 同一条退路。
        index = int(code, 16) if re.fullmatch(r"[0-9A-Fa-f]+", code) else \
            by_name.get(str(row.get("名称", "")).strip(), -1)
        if not 0 <= index < n:
            continue
        base = index * AIL2_STRIDE
        for off, name in AIL2_STATS.items():
            raw = data[base + off:base + off + 4]
            value = int.from_bytes(raw, "little", signed=True)
            # **enc 是正本**：它是当前安装版本的真值，而配套 txt 是某个版本导出
            # 的快照 —— 换个版本 txt 就不对了。旧值挪到 `<字段>·txt` 留作对照。
            if row.get(name) not in (None, "") and row.get(name) != value:
                row[f"{name}·txt"] = row[name]
            row[name] = value
        for off, (name, enum) in AIL2_RULES.items():
            code_v = int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
            row[name] = enum.get(code_v, code_v)
        for off, name in AIL2_EFFECT.items():
            row[name] = int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
        # 抗性索引0/9/10也被官方武器附加作用使用，不能只保留八项可见抗性。
        row["抗性原值"] = [int.from_bytes(data[base+off:base+off+4], 'little', signed=True)
                         for off in range(116, 160, 4)]
        row["攻击特效"] = [dict(码=int.from_bytes(data[base+100+i*4:base+104+i*4], 'little'),
            概率=int.from_bytes(data[base+80+i*4:base+84+i*4], 'little')) for i in range(5)
            if int.from_bytes(data[base+80+i*4:base+84+i*4], 'little') > 0]
        attach_use_effects(row, data, base)
        hit += 1
    return hit


def attach_use_effects(row: dict, data: bytes, base: int) -> None:
    """把使用特效与完整五槽动画挂到记录；保留使用动画首槽旧接口。

    ⚠️ **只收概率 > 0 的**。空槽位在 enc 里不是 0 —— 化毒散五个槽全填着 40，
    但只有第一个的概率是 100。**拿特效码判空会把四个幽灵特效算进来。**
    这和绝学表那条「特效 0 是中毒，判空要看概率」是同一个坑。
    """
    effects = []
    for prob_off, code_off in zip(AIL2_EFFECT_PROB, AIL2_EFFECT_CODE):
        prob = int.from_bytes(data[base + prob_off:base + prob_off + 4], "little", signed=True)
        if prob <= 0:
            continue
        code = int.from_bytes(data[base + code_off:base + code_off + 4], "little", signed=True)
        effects.append({"特效": ITEM_EFFECTS.get(code, code), "特效码": code, "概率": prob})
    row["使用特效"] = effects

    blob = data[base + AIL2_USE_ANIM_OFF:base + AIL2_USE_ANIM_OFF + AIL2_USE_ANIM_LEN]
    name = blob.split(b"\x00")[0].decode("ascii", "ignore").strip()
    row["使用动画"] = name or None
    row["使用动画槽"] = animation_slots(data, base, 716, 766, 786, 806, 826)
    row["使用动画槽上限"] = int.from_bytes(data[base + 692:base + 696], "little", signed=True)


def attach_desc(rows: list[dict], key: str, descs: list[str],
                names: list[str] | None = None) -> int:
    """按物品编号补 `说明繁`。

    ⚠️ **这是给渲染用的正本** —— 配套 txt 里的「详述」是简体，
    而字库按 Big5 索引，简体字画不出来。见 `docs/专题/简繁体.md`。

    ⚠️ 少数条目的代码格**不是十六进制** —— 例如聚元散那格填的是「买炼」
    （获得方式，资料作者手滑）。这种退回**按名称在 Ail2 名称表里反查下标**；
    名称繁简同形时（聚元散三个字就是）能直接命中。
    """
    hit = 0
    by_name = {}
    if names:
        for i, n in enumerate(names):
            by_name.setdefault(n.strip(), i)
    for row in rows:
        code = half(str(row.get(key, "")))
        index = int(code, 16) if re.fullmatch(r"[0-9A-Fa-f]+", code) else \
            by_name.get(str(row.get("名称", "")).strip(), -1)
        if index < 0:
            continue
        text = descs[index] if index < len(descs) else ""
        if text:
            row["说明繁"] = text
            if not row.get("名称繁") and names and index < len(names):
                row["名称繁"] = names[index]
            hit += 1
    return hit


def attach_traditional(rows: list[dict], key: str, names: list[str]) -> int:
    """按物品编号（十六进制）给每条补 `名称繁`。返回补上的条数。

    字数对不上的**不采用** —— 那说明 join 错位或 MOD 改过名，宁可留空让界面退回简体，
    也不要贴一个张冠李戴的名字。
    """
    hit = 0
    for row in rows:
        code = half(row.get(key, ""))
        if not re.fullmatch(r"[0-9A-Fa-f]+", code):
            continue
        index = int(code, 16)
        name = names[index] if 0 <= index < len(names) else ""
        if name and len(name) == len(str(row.get("名称", ""))):
            row["名称繁"] = name
            hit += 1
    return hit


#: **绝不能数值化的字段** —— 它们是**十六进制字符串**，不是数。
#:
#: ⚠️ 这条曾经悄悄毁掉一大批数据：`as_num` 的正则是 `^[+-]?\d+`，
#: 遇到 `1A` 会截成 **1**、遇到 `FB` 直接失败。于是
#:   * `attach_traditional` 拿 1 当下标 → 「灵剑朱雀」被贴上「護身匕首」，
#:     283 条繁体名里 56 条张冠李戴；
#:   * `equip_slot` 也按被截断的编号判槽位。
#: 纯数字的十六进制（如 `65`）碰巧不受影响，所以错误只在含字母的编号上出现，
#: 更难发现。**保持字符串原样，用到时再 `int(half(x), 16)`。**
HEX_KEYS = frozenset({"物品编号", "物品代码", "绝学代码", "代码"})


def simplified_index(name: str, code_key: str) -> dict[int, str]:
    """`data/<name>.json` → {代码: 简体名称}。

    **只用来补简体名，不做别的。** 骨架一律从 enc 枚举（见 `enumerate_*`），
    但前端把**简体名称当主键**用（`inventory.js` / `partyState.js` /
    `MenuScreen.js` 都是 `find(r => r.名称 === name)`），而 enc 里只有 Big5 繁体。

    ⚠️ 这份 json 是 **300块** 配套资料解出来的。官方版是 300块 的真子集，
    所以按代码反查命中率很高；查不到的条目退回繁体名（界面照样能画，
    只是可能缺字）。彻底的解法是把前端主键换成代码，那是另一件事。
    """
    out = {}
    for row in json.loads((NAMES_DIR / f"{name}.json").read_text()):
        code = half(str(row.get(code_key, "")))
        label = str(row.get("名称", "")).strip()
        if label and re.fullmatch(r"[0-9A-Fa-f]+", code):
            out.setdefault(int(code, 16), label)
    return out


def enumerate_items(data: bytes) -> list[dict]:
    """**从 `Ail2.ENC` 枚举全部物品** —— 骨架来自 enc，不是配套 txt。

    ⚠️ **这是 2026-08-30 的改动**：原先拿 `data/items.json`（300块 配套 txt
    解出来的）当骨架，enc 只做数值覆盖。那样换成官方版就散架 ——
    官方版没有配套资料，而且 300块 多出来的 1315 件物品会照样出现在产物里。
    现在反过来：enc 枚举出什么就是什么，txt 只用来补简体名。
    """
    names = load_item_names()
    descs = load_item_descs()
    simple = {**simplified_index("items", "物品代码"),
              **simplified_index("equipment", "物品编号")}
    rows = []
    for index in range(len(data) // AIL2_STRIDE):
        trad = names[index] if index < len(names) else ""
        if not trad.strip():
            continue
        base = index * AIL2_STRIDE
        row = {"物品编号": f"{index:X}", "名称": simple.get(index, trad), "名称繁": trad}
        if index < len(descs) and descs[index]:
            row["说明繁"] = descs[index]
        for off, field in AIL2_STATS.items():
            row[field] = int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
        for off, (field, enum) in AIL2_RULES.items():
            code = int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
            row[field] = enum.get(code, code)
        for off, field in AIL2_EFFECT.items():
            row[field] = int.from_bytes(data[base + off:base + off + 4], "little", signed=True)
        row["攻击特效"] = [dict(码=int.from_bytes(data[base+100+i*4:base+104+i*4], 'little'),
            概率=int.from_bytes(data[base+80+i*4:base+84+i*4], 'little')) for i in range(5)
            if int.from_bytes(data[base+80+i*4:base+84+i*4], 'little') > 0]
        attach_use_effects(row, data, base)
        refine = AIL2_REFINE.get(data[base + AIL2_REFINE_OFF], data[base + AIL2_REFINE_OFF])
        row["炼化类型"] = refine
        row["槽位"] = SLOT_BY_CATEGORY.get(row["物品类别"])
        if row["槽位"]:
            # None（并集为所有人）与「类型不在表里」都写 null，前端按「不限制」处理。
            row["可装备者"] = EQUIP_BY_REFINE.get(refine)
        rows.append(row)
    return rows


def enumerate_skills(data: bytes) -> list[dict]:
    """**从 `Firttech.enc` 枚举全部绝学** —— 骨架来自 enc，不是配套 txt。

    官方按程序实际下标建键，首列只作原始记录标识；详见firttech_offsets。
    """
    simple = simplified_index("skills", "绝学代码")
    magiccon = load_skill_names()
    descs = load_skill_descs()
    rows = []
    for index in range(len(data) // FIRTTECH_STRIDE):
        base = index * FIRTTECH_STRIDE
        raw_code = int.from_bytes(data[base:base + 4], "little")
        code = index if SRC.名 == "官方" else raw_code
        blob = data[base + 4:base + 24].split(b"\x00")[0]
        try:
            trad = blob.decode("big5").strip()
        except UnicodeDecodeError:
            trad = ""
        if not trad:
            trad = magiccon[code] if code < len(magiccon) else ""
        if not trad.strip():
            continue
        row = {"绝学代码": f"{code:X}", "名称": simple.get(raw_code, trad), "名称繁": trad}
        if index < len(descs) and descs[index]:
            row["说明繁"] = descs[index]
        rows.append(row)
    return rows


def load_normal_attacks(groups: list[dict]) -> dict:
    """官方0x422f57/0x423acb：普攻用人物代码索引Firttech，无名称也不能丢。"""
    data = decrypt("Firttech.enc")
    codes = sorted(set(range(1, 9)) | {e["代码"] for g in groups for e in g["敌人"] if e["代码"]})
    rows = [{"绝学代码": f"{code:X}", "名称繁": "普通攻击"} for code in codes]
    attach_skill_fields(rows, data)
    return {str(code): row for code, row in zip(codes, rows) if row.get("攻击动作")}


def equip_slot(code) -> str:
    """⚠️ **已弃用**（2026-08-30）：改用 `物品类别`（`Ail2.ENC` +24）。

    这套「按物品编号分段」的推断在官方版全表上是错的 —— 类别为武器的有 224 件，
    而编号 ≤100 的那一段只装得下约 100 件。留着仅供追溯，别再调用。
    """
    # ⚠️ **物品编号是十六进制**（见 CLAUDE.md 的编号坑）。绝不能先按十进制试 ——
    # 布袍的编号是 "65"，十进制解成 65 就落进兵刃段了，十六进制才是 0x65=101。
    try:
        n = int(half(str(code)), 16)
    except ValueError:
        return "饰物"
    for bound, slot in SLOT_BOUNDS:
        if n <= bound:
            return slot
    return "饰物"


def name_lookup(rows: list[dict], code_key: str) -> dict[str, str]:
    """{名称: 代码}，简体名与繁体名都收。**同名只留第一条。**

    ⚠️ 名称本来就不足以定位（官方版装备 44 条重名、用器 14 条、绝学繁体名
    37 条），所以这张表只用来把**人写的夹具**翻译成代码 —— 开局装备、
    角色已学绝学这类。运行时的查找一律走代码，见 `game/src/systems/catalog.js`。
    """
    out = {}
    for row in rows:
        code = str(row.get(code_key, "")).upper()
        for label in (row.get("名称"), row.get("名称繁")):
            key = str(label or "").strip()
            if key and code:
                out.setdefault(key, code)
    return out


#: 资料作者给同名绝学加的区分括号：「气愈之术（我方主力）」→「气愈之术」。
#: **括号不是绝学名的一部分**，`characters.json` 那边写的是不带括号的。
BARE = re.compile(r"[（(].*$")


def resolve_gear(rows: list[dict]) -> dict:
    """开局装备的名称 → 代码。查不到就**留着名称并打印警告**，不要静默丢。"""
    table = name_lookup(rows, "物品编号")
    out, miss = {}, []
    for who, slots in START_GEAR.items():
        out[who] = {}
        for slot, label in slots.items():
            code = table.get(label)
            if code is None:
                miss.append(f"{who}.{slot}={label}")
            out[who][slot] = code
    if miss:
        print(f"    ⚠️ 开局装备有 {len(miss)} 件查不到代码：{'、'.join(miss)}")
    return out


#: 装备的槽位边界（**物品编号**，十六进制原值转十进制后比较）。
#:
#: ⚠️ **「槽位」不是原始字段** —— 配套 txt、花丛那份 xlsx、`data/equipment.json`
#: 三处都没有这一列。它是从**物品编号的分段**推断出来的，判据四条：
#:
#:   1. 编号严格分段，段与段之间有空号（18|21、40|41、58|61、78|81、98|100、
#:      154|156、156|161）。
#:   2. 每一段的「可装备者」自成一组：兵刃按角色分段（1–18 夏霍、21–40 冰、
#:      41–58 封高、61–78 古萧、81–98 璇葛、100 武），护甲与饰物则是
#:      「所有人」或多角色混排。
#:   3. 名称语义 100% 吻合：≤100 全是刀剑枪戟绫索法珠，101–156 全是
#:      袍/衣/甲/铠/裘，≥161 全是佩/符/簪/珠/石/魂石。
#:   4. 两个已知锚点对得上：护身匕首(1)=兵刃、布袍(101)=护甲，正是 START_GEAR。
#:
#: 证据很强，但**终究是推断**。哪天拿到原始槽位表，以那个为准。
SLOT_BOUNDS = ((0x64, "兵刃"), (0xA0, "护甲"))


#: `物品类别`（`Ail2.ENC` +24，原始字段）→ 菜单槽位。用器与杂类不占槽。
SLOT_BY_CATEGORY = {"武器": "兵刃", "防具": "护甲", "饰物": "饰物"}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--outdir", type=Path, default=ROOT / "game/public/assets/data")
    ap.add_argument("--version", choices=tuple(VERSIONS), default="官方",
                    help="导哪个安装的数据。默认官方版；300块 仅作对照")
    ap.add_argument("--multimedia", type=Path, default=None,
                    help="该版本安装的 multimedia 目录（不给用 VERSIONS 里的默认位置；一键提取用）")
    ap.add_argument("--names-dir", type=Path, default=None,
                    help="简体名表 items/equipment/skills.json 所在目录（简体补丁；不给用仓库根 data/）")
    args = ap.parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    global SRC, NAMES_DIR
    SRC = VERSIONS[args.version]
    if args.multimedia:
        SRC = 版本(SRC.名, args.multimedia)
    if args.names_dir:
        NAMES_DIR = args.names_dir
    if not (SRC.multimedia / "public").is_dir():
        print(f"找不到 {SRC.名} 版的 public/：{SRC.multimedia}", file=sys.stderr)
        return 1
    print(f"数据源：{SRC.名}版  {SRC.multimedia}")

    # 先枚举，因为「开局装备」与角色的「绝学」都要按名称换成代码。
    goods = enumerate_items(decrypt("Ail2.ENC"))
    skills = enumerate_skills(decrypt("Firttech.enc"))
    fields = attach_skill_fields(skills, decrypt("Firttech.enc"))
    characters = load_characters()
    known = sum(len(rec["绝学"]) for rec in characters.values())
    groups = load_encounter_groups()

    bundle = {
        "生成自": "tools/export_gamedata.py",
        # ⚠️ **产物是哪个版本的**。两版结构相同、数值大不同（绝学指数改了 39%、
        # 历练门槛整表重写），混用会让战斗数值静悄悄地错。见 `docs/专题/官方版对照.md`。
        "版本": SRC.名,
        "说明": "数值的唯一来源。公式在 game/src/systems/formulas.js，那里只放算法不放数。",
        "常数": CONSTANTS,
        "战斗规则": __import__('battle_rules').load_rules(SRC.multimedia.parent / 'exe/RPG.exe', SKILL_EFFECT)
            if SRC.名 == '官方' else None,
        # 判红用。兵刃五类是硬结论，护甲饰物是按类型取的并集 —— 见 EQUIP_BY_REFINE。
        "炼化类型可装备者": EQUIP_BY_REFINE,
        "队伍顺序": list(PARTY),
        "角色": characters,
        "开局装备": resolve_gear(goods),
        "成长": load_growth(),
        "历练门槛": load_levelup(),
        # ⚠️ **两个版本的系数表不通用**，各自 8/8 验证过，用错版本八项全错：
        # 200 块用「原版」、300 块用「本补丁」（`formulas.RESIST_TABLE` 选哪张）。
        # 这张表没在 enc/exe 里定位到，作为已验证的自有常量发布（WUNEI_RESIST）。
        "五内抗性系数": load_wunei_resist(),
        "商店售货": load_shops(),
        "遇敌组": groups,
        "遇敌群": load_encounter_swarms(),
        # 遇敌组点过名的敌人。⚠️ 名称是 **Big5 繁体**（渲染正本），
        # 与我方角色一样，字库按 Big5 码位索引 —— 见 `docs/专题/简繁体.md`。
        "敌人": load_enemies(groups),
        "敌人策略": load_enemy_ai(),
        "普通攻击": load_normal_attacks(groups),
        "绝学习得": load_skill_unlock(),
    }
    (args.outdir / "gamedata.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=1))

    # **骨架一律从 enc 枚举**（2026-08-30 起）。原先拿 `data/*.json`
    # （300块 配套 txt 解出来的）当骨架、enc 只做覆盖 —— 那样换官方版就散架。
    # 现在 txt 只剩一个用途：给每条补个简体名，因为前端拿简体名当主键。
    equipment = [r for r in goods if r["槽位"]]
    items = [r for r in goods if not r["槽位"]]
    for name, rows in (("equipment", equipment), ("items", items), ("skills", skills)):
        (args.outdir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        named = sum(1 for r in rows if r["名称"] != r["名称繁"])
        desc = sum(1 for r in rows if r.get("说明繁"))
        print(f"  {name}.json  {len(rows)} 条，简体名 {named} 条，繁体说明 {desc} 条")
    print(f"    ↳ 从 Firttech.enc 补了 {fields} 条绝学的作用场合/消耗/蓄劲回气/特效"
          f"；角色已学绝学 {known} 门已换成代码")

    print(f"  gamedata.json  角色 {len(bundle['角色'])} · 成长 {len(bundle['成长'])} · "
          f"历练门槛 {len(bundle['历练门槛'])} 档 · "
          f"抗性系数 {list(bundle['五内抗性系数'])} · 绝学习得 {len(bundle['绝学习得'])} 条")
    total = sum(f.stat().st_size for f in args.outdir.glob("*.json"))
    print(f"-> {args.outdir}  共 {total/1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

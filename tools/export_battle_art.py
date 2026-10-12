#!/usr/bin/env python3
"""按**数据反查**导出战斗动画包 → `game/public/assets/<包名>/`。

## 为什么要有这个工具

战斗单位「在不在场上」过三关：队伍里有他 → 有素材对照 → **素材导出并预载**。
第三关从前是**手工挑几个文件**导，于是每来一个新队友、新敌人就得再修一次 bug
（冰璃入队了不上场、遇敌组要 5 个敌人只出 4 个）。

这个工具从角色、敌人、遇敌、全部绝学和物品表收集依赖，不按样本技能挑包。

## 导什么

* **我方**：`PARTY_ART` 里七个队友的全部 6 类包。判据见下。总共 3.5 MB，无条件全导。
* **敌方/背景**：按敌人与遇敌全表导出；--maps仅提供诊断参考。
* **绝学/物品**：五个动作和完整五槽动画；空槽保留编号但不请求资源。

## 队友的素材编号 —— **就是「代码 ×10」**

⚠️ **2026-09-16 订正：这张表此前把代码 2 与 3 写反了，做了一次多余的互换**，
后果是**冰璃在战斗里用封鈴笙的立绘与头像、封鈴笙用冰璃的** ——
两个队友互相换脸，而画面上看着只是「谁谁长得不对」，不报任何错。

错因是**人物代码认反了**（素材那半边一直是对的）。原作角色表
`public/Api.enc` 每条 `+12` 就是 Big5 名称，读出来白纸黑字：

    代码1「夏侯儀」 代码2「冰璃」 代码3「封玲笙」 代码4「慕容璇璣」
    代码5「古倫德」 代码6「葛雲衣」 代码7「霍雍」 代码8「冰璃」(位阶60，另一形态)

判据三重自洽：

1. **素材本身**：七张立绘与七套站立图并排比对 —— `0020` 是白发蓝白衣（冰璃）、
   `0030` 是绿衣蓝黑发髻双绸带（封鈴笙）。
2. **`EndDir.DAT` 只有 8 条**（`END0010`~`END0070` 加一个 `END`），正好七个队友。
3. **代码 8 = 冰璃另一形态** ↔ 素材 `0080` 与 `0020` **字节数完全相同** ↔
   头像 `ITF0002` 的**第 7 帧复用第 1 帧的图**（帧1＝冰璃）。
   三样东西同时指向同一个人，把「代码×10」钉死了。

    | 代码 | 人 | 素材 | 对上的特征 |
    |---|---|---|---|
    | 1 | 夏侯儀 | 0010 | 金发、白袍、持匕首 |
    | 2 | 冰璃 | 0020 | 白发、蓝白衣 |
    | 3 | 封鈴笙 | 0030 | 绿衣、蓝黑发髻、双绸带 |
    | 4 | 慕容璇璣 | 0040 | 棕辫、橙紫衣、托蓝球 |
    | 5 | 古倫德 | 0050 | 金发、盔甲、持长枪 |
    | 6 | 葛雲衣 | 0060 | 棕发丸子头、粉红斗篷 |
    | 7 | 霍雍 | 0070 | 黑长发、深蓝紫衣 |

⚠️ `0040` 与 `0060` **没有 `MOV` 包**，`0080` 没有 `ATT`/`END`（它是冰璃的
另一形态，不是第八个队友）。缺包不是错，跳过即可。

用法:
    python3 export_battle_art.py <fight 目录> <out 目录> \
        --gamedata game/public/assets/data/gamedata.json \
        --maps-dir game/public/assets/maps --maps MP0212,MP0304
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import platform
from pathlib import Path

import dat_unpack

#: 人物代码 → 战斗素材编号。判据见文件头。
PARTY_ART = {
    1: "0010",   # 夏侯儀
    2: "0020",   # 冰璃
    3: "0030",   # 封鈴笙
    4: "0040",   # 慕容璇璣
    5: "0050",   # 古倫德
    6: "0060",   # 葛雲衣
    7: "0070",   # 霍雍
}

#: 按人物与动作记录构建；敌方同样有闪避、失误、移动等动作。
#: ⚠️ **`MIS` 不是「移动」，是「攻击落空」**（2026-09-17 认清）：
#: `ATT0010` 与 `MIS0010` 的 19 张图片**逐字节完全相同**、帧布局相同，
#: 只差帧 1 的 `effect_file`（1 vs 2）与两处音效 —— 同一套挥刀美术挂不同特效。
#: 这也解释了为什么 `AttDir`/`MisDir`/`RedDir` 三张表的编号集合完全一样。
#: `RED` 是蓄劲动作，官方分支依据见docs/判据/战斗动画数据.md。
PARTY_KINDS = ("STN", "ATT", "MIS", "RED", "HIT", "DEF", "MOV", "BAK", "END", "LUP")
FOE_KINDS = ("STN", "ATT", "MIS", "RED", "HIT", "DEF", "MOV", "BAK")

ARCHIVES = {
    "STN": "StnDir.DAT", "ATT": "AttDir.DAT", "HIT": "HitDir.DAT",
    "DEF": "DefDir.DAT", "MOV": "MovDir.DAT", "END": "EndDir.DAT", "LUP": "LupDir.DAT",
    "MIS": "MisDir.DAT", "RED": "RedDir.DAT", "BAK": "BakDir.DAT",
    # ⭐ **绝学的特效与投射物** —— `Firttech.enc +250 动画文件一` 点名的就是
    # 这两个归档里的包（`EFF3031` 法术特效、`SHO10041` 投射物）。
    # 漏掉它们的表现是**绝学打出去一点特效都没有**，只有一条 404。
    "EFF": "EffDir.DAT", "SHO": "ShoDir.DAT",
    # 施法动作模板（`MAG0010` 那种）与用物品的动画。
    "MAG": "MagDir.DAT", "USE": "UseDir.DAT",
    # 战斗背景。`FLR6000`~`FLR6066`，按遇敌群的 `战斗背景地图` 反查。
    "FLR": "FlrDir.DAT",
    # 战斗界面（状态条、菜单板、结算框…）。**整包导，不挑清单** —— 见 ui_packs()。
    "ITF": "ItfDir.DAT",
}


def script_swarms(maps_dir: Path, maps: list[str]) -> set[int]:
    """这些图的剧情脚本会打哪些仗 —— 返回 **`op55` 的 `swarm`，即遇敌群编号**。

    ⚠️ **`swarm` 是遇敌群编号，不是遇敌组**（2026-09-13 订正，此前这里读错）。
    判据是羅喉城五个「宿座」：`MP2403C1`~`C5` 图名写着太白/歲星/辰星/熒惑/鎮星
    （金木水火土五星），那五张图上的 `swarm` 查**遇敌群**正好得到金木水火土，
    查**遇敌组**则整体错开一位。完整判据见 `docs/专题/战斗.md` §1.2。
    """
    swarms = set()
    for mid in maps:
        path = maps_dir / mid / "map.json"
        if not path.is_file():
            print(f"⚠ {mid} 没有 map.json，跳过", file=sys.stderr)
            continue
        meta = json.loads(path.read_text(encoding="utf-8"))
        for actions in (meta.get("scripts") or {}).values():
            for act in actions:
                if act.get("type") == "battle":
                    swarms.add(int(act["swarm"]))
    return swarms


def groups_of(gamedata: dict, swarms: set[int]) -> set[int]:
    """遇敌群 → 它可能抽到的**全部**遇敌组。

    ⚠️ 要取**全部**而不是抽一个：地区遭遇的群带着十个组，随便哪一个都可能
    打到，素材少导一个的表现是「那个敌人在数据里有、画面上没有」。
    """
    by_num = {int(s["编号"]): s for s in gamedata.get("遇敌群", []) if "编号" in s}
    out = set()
    for n in sorted(swarms):
        swarm = by_num.get(n)
        if not swarm:
            print(f"⚠ 遇敌群 {n} 不在 gamedata 里", file=sys.stderr)
            continue
        out.update(int(g) for g in swarm.get("遇敌组") or [])
        out.update(int(g) for g in swarm.get("附加遇敌组") or [])
    return out


def foe_arts(gamedata: dict, groups: set[int]) -> set[str]:
    """这些遇敌组会用到哪些敌人素材。"""
    by_num = {int(g["编号"]): g for g in gamedata.get("遇敌组", []) if "编号" in g}
    foes = gamedata.get("敌人", {})
    arts = set()
    for num in sorted(groups):
        group = by_num.get(num)
        if not group:
            print(f"⚠ 遇敌组 {num} 不在 gamedata 里", file=sys.stderr)
            continue
        for foe in group.get("敌人") or []:
            art = foes.get(str(foe["代码"]), {}).get("素材")
            if art:
                arts.add(str(art))
    return arts


#: 战斗地面素材的编号上界：`FlrDir.DAT` 里是 `FLR6000`~`FLR6066`，共 67 张。
#: 全库遇敌群的 `战斗背景地图` 取值域正好 0~65 —— 这个吻合本身就是判据。
FLOOR_MAX = 66


def floor_packs(gamedata: dict, swarms: set[int]) -> set[str]:
    """这些遇敌群要哪几张**战斗背景** —— `战斗背景地图 n` → `FLR60{n:02d}`。

    判据是渲染出来逐张核对的（同一段剧情上四条全中）：
    群102 離火神訣發動→7 木地板破屋 / 群103 下車遭遇戰→8 洞外岩地 /
    群2 迦夏之窟洞內→9 洞内石门 / 群104 冰璃登場→10 蓝色石殿。

    ⚠️ **不要写死清单。** 判据表 F 组：`BGM_FILES`/`SFX_FILES` 都在手写清单上
    栽过（八首曲子、23 个音效静默地没导）。
    """
    by_num = {int(s["编号"]): s for s in gamedata.get("遇敌群", []) if "编号" in s}
    keys = set()
    for n in sorted(swarms):
        swarm = by_num.get(n)
        if not swarm:
            continue
        bg = (swarm.get("战斗背景地图") or [None])[0]
        if not isinstance(bg, int) or not 0 <= bg <= FLOOR_MAX:
            print(f"⚠ 遇敌群 {n}「{swarm.get('名称')}」的战斗背景地图是 {bg}，"
                  f"解不出 FLR60xx（有效 0~{FLOOR_MAX}）", file=sys.stderr)
            continue
        keys.add(f"FLR60{bg:02d}")
    return keys


def ui_packs(fight: Path) -> set[str]:
    """战斗界面素材 —— **整个 `ItfDir` 全导，不挑清单**。

    判据表 F 组：手写清单类常量必漏（`BGM_FILES` 漏过八首曲子、`SFX_FILES`
    漏过 23 个音效，表现都是「静默地不响/不换」）。而界面素材没有「拿数据
    反查求差集」的路子可走 —— exe 只点名了 45 件中的 33 件，另外 63 件是
    循环或别处构造的，扫不到。

    好在**整个归档只有 7.7 MB**，全导最省事也最不容易错。

    ⚠️ 这条「全导」曾经**不等于全导** —— `dat_unpack` 会按条目自己声明的
    长度做合法性检查，而归档末尾几条声明的长度超过剩余字节，于是
    `ITF210`/`ITF501`/`ITF502`/`ITF503` 被**静默丢掉**。后果是官方版被判成
    「没有絕學的列表板与咒法／絕技板」，还差点去 300块 MOD 拿（那份是
    **简体重画**的「绝技」）。修在 `dat_unpack._bounded`：长度一律拿
    「到下一条的偏移」兜底。全库共漏了 12 条。
    """
    archive = fight / ARCHIVES["ITF"]
    if not archive.is_file():
        print(f"⚠ 找不到 {archive}", file=sys.stderr)
        return set()
    with archive.open("rb") as fh:
        entries = dat_unpack.read_entries(fh, archive.stat().st_size)
    names = {e.name.rsplit(".", 1)[0].upper() for e in entries
             if e.name.upper().endswith(".SF2")}
    print(f"战斗界面素材 {len(names)} 件（整包导）")
    return names


#: 绝学记录里的五个动作字段（`Firttech.enc` +156/166/176/186/196，
#: 字段名与偏移抄自 exe 规格区）。值是**素材包名**，`NULL` = 这一门没有。
SKILL_ACTION_FIELDS = ("蓄劲动作", "移动动作", "攻击动作", "失误动作", "归位动作")

def pack_name(value) -> str | None:
    name = str(value or "").strip().upper()
    return name if name and name not in ("NULL", "DEFAULT") else None


def row_packs(row: dict, slot_field: str, actions=()) -> set[str]:
    """五槽必须来自新版生成器；不能悄悄降回只读第一槽。"""
    slots = row.get(slot_field)
    if not isinstance(slots, list) or [s.get("槽号") for s in slots] != [1, 2, 3, 4, 5]:
        raise ValueError(f"{row.get('名称')}: 缺少完整{slot_field}，先运行export_gamedata.py")
    if not isinstance(row.get(slot_field + "上限"), int) or any(not isinstance(s.get("触发号"), int) for s in slots):
        raise ValueError(f"{row.get('名称')}: 缺少动画槽上限/触发号，先运行export_gamedata.py")
    return {name for value in [*(row.get(f) for f in actions),
                               *(s.get("文件") for s in slots)]
            if (name := pack_name(value))}


def reference_packs(data_dir: Path) -> dict[str, list[dict]]:
    """记录每项资源的所有引用者，包含装备表，避免未来新增用器被漏掉。"""
    refs = {}
    for table, field, code, actions in (
            ("skills", "动画槽", "绝学代码", SKILL_ACTION_FIELDS),
            ("items", "使用动画槽", "物品编号", ()),
            ("equipment", "使用动画槽", "物品编号", ())):
        rows = json.loads((data_dir / f"{table}.json").read_text(encoding="utf-8"))
        for row in rows:
            for name in sorted(row_packs(row, field, actions)):
                refs.setdefault(name, []).append({"table": table, "code": row[code],
                                                 "name": row["名称繁"]})
    # 人物默认施法/移动/归位也是运行依赖，不能靠单门绝学碰巧引用才导出。
    bundle_path = data_dir / "gamedata.json"
    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        rules = bundle.get("战斗规则", {})
        for code, row in rules.get("场方效果", {}).items():
            if key := row.get("动画"):
                refs.setdefault(key, []).append({"table": "fields", "code": code, "name": "场方持续演出"})
        for code, row in rules.get("状态演出", {}).items():
            for key in filter(None, row.values()):
                refs.setdefault(key, []).append({"table": "states", "code": code, "name": "人物状态演出"})
        for name, row in bundle.get("角色", {}).items():
            art = PARTY_ART.get(int(row["代码"]))
            if art:
                for kind in ("END", "LUP"):
                    refs.setdefault(f"{kind}{art}", []).append({"table": "characters", "code": str(row["代码"]), "name": name})
            for field in ("移动动作", "归位动作", "默认施法动作"):
                if key := pack_name(row.get(field)):
                    refs.setdefault(key, []).append({"table": "characters", "code": str(row["代码"]), "name": name})
        # 默认人物动作也进入持续闭合检查；散装补丁与归档同等纳入来源。
        substitutes = json.loads((Path(__file__).resolve().parents[1] / "game/src/systems/battleArt.json").read_text())
        for code, row in bundle.get("敌人", {}).items():
            for field in ("移动动作", "归位动作", "默认蓄劲动作", "默认施法动作"):
                if key := pack_name(row.get(field)):
                    refs.setdefault(key, []).append({"table": "enemies", "code": code, "name": row["名称繁"]})
            art = substitutes.get(str(row["素材"]), str(row["素材"]))
            for kind in ('STN', 'HIT', 'DEF'):
                key = f'{kind}{art}'
                refs.setdefault(key, []).append({"table": "enemies", "code": code, "name": row["名称繁"]})
        for code, row in bundle.get("普通攻击", {}).items():
            for name in sorted(row_packs(row, "动画槽", SKILL_ACTION_FIELDS)):
                refs.setdefault(name, []).append({"table": "normal-attacks", "code": code,
                                                 "name": "普通攻击"})
    return dict(sorted(refs.items()))


def pack_errors(root: Path, name: str) -> list[str]:
    """包目录存在不足以通过：JSON、图片、音效和帧的图片引用都必须闭合。"""
    folder = root / name
    try:
        doc = json.loads((folder / "anim.json").read_text(encoding="utf-8"))
        images = {x["index"] for x in doc["images"]}
        files = [x["file"] for x in doc.get("sounds", [])]
        errors = []
        if name.startswith(('MOV', 'BAK')) and not doc.get('motion'):
            errors.append('missing motion header: run --refresh-motion')
        if doc.get("atlas"):
            atlas = json.loads((folder / doc["atlas"]["json"]).read_text(encoding="utf-8"))
            files.extend(t["image"] for t in atlas["textures"])
            frames = {f["filename"] for t in atlas["textures"] for f in t["frames"]}
            errors.extend(f"missing atlas frame: {Path(x['file']).stem}" for x in doc["images"]
                          if Path(x["file"]).stem not in frames)
        else:
            files.extend(x["file"] for x in doc["images"])
        errors.extend(f"missing file: {file}" for file in files if not (folder / file).is_file())
        for frame in doc["frames"]:
            for layer in frame.get("layers", []):
                if layer["image_index"] not in images:
                    errors.append(f"frame {frame['index']}: image {layer['image_index']} not exported")
        return sorted(set(errors))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [str(exc)]


def source_inventory(fight: Path, refs: dict) -> dict:
    """源分母独立于导出结果；扫描整个fight的DAT和散装SF2，保留歧义。"""
    sources = {}
    archives = []
    for archive in sorted(fight.iterdir()):
        if not archive.is_file() or archive.suffix.upper() != ".DAT":
            continue
        archives.append({"file": archive.name,
                         "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
        with archive.open('rb') as fh:
            for entry in dat_unpack.read_entries(fh, archive.stat().st_size):
                name = Path(entry.name).stem.upper()
                if name not in refs or Path(entry.name).suffix.upper() != '.SF2':
                    continue
                fh.seek(entry.offset)
                raw = fh.read(entry.size)
                sources.setdefault(name, []).append({"archive": archive.name,
                    "entry": entry.name, "offset": entry.offset, "bytes": entry.size,
                    "sha256": hashlib.sha256(raw).hexdigest()})
    # 原作目录的散装文件也有仅此一份的新增素材，不能只查归档同名项。
    for path in sorted(fight.rglob('*')):
        if path.is_file() and path.suffix.upper() == '.SF2' and path.stem.upper() in refs:
            raw = path.read_bytes()
            sources.setdefault(path.stem.upper(), []).append({"loose": str(path.relative_to(fight)),
                "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    gaps = {name: refs[name] for name in refs if name not in sources}
    return {"source": str(fight), "archives": archives, "sources": sources,
            "sourceGaps": gaps,
            "boundary": "缺口只表示此安装目录未找到；没有批准替代素材或运行时回退。"}


def check_references(root: Path, refs: dict, source_manifest: dict | None = None) -> dict:
    failures = {name: errors for name in refs if (errors := pack_errors(root, name))}
    # 已取证的源缺口仍显示为缺口；只对原引用者不变且整个产物缺失的情况分类。
    # 不容许用它隐藏现有包损坏、增加引用者，或任意新增的漏导。
    known = {}
    for name, callers in (source_manifest or {}).get("sourceGaps", {}).items():
        if name in failures and refs.get(name) == callers and not (root / name).exists():
            known[name] = failures.pop(name)
    return {"references": refs, "count": len(refs), "failures": failures, "sourceGaps": known,
            "boundary": "仅验证数据引用到JSON/图片/音效闭合，不证明播放时序与原作一致。"}


def skill_action_packs(skills_path: Path) -> set[str]:
    """绝学**自己点名**的动作包 —— 拿数据反查，不要手写清单。

    ⚠️ 判据表 F 组：写清单类常量必须拿数据反查求差集。此前只按
    `PARTY_ART × PARTY_KINDS` 导（`ATT0010`、`RED0010`…），而绝学点名的是
    **带门号的**（摄魂鬼爪是 `ATT0011`/`RED0011`/`MIS0011`）——
    一个都没导。表现是控制台刷 `ATT0011-anim` 404，
    **战斗里绝学没有出招姿势、蓄劲期间人站着不动**（用户 2026-09-18 报）。

    ⚠️ `NULL` 是「这一门没有这个动作」，不是包名。
    """
    if not skills_path.is_file():
        print(f"⚠ 找不到 {skills_path}，绝学动作包这一批不导", file=sys.stderr)
        return set()
    rows = json.loads(skills_path.read_text(encoding="utf-8"))
    packs = set()
    for row in rows:
        packs |= row_packs(row, "动画槽", SKILL_ACTION_FIELDS)
    return packs


def all_foe_arts(gamedata: dict) -> set[str]:
    """**敌人表里每一条的素材** —— 不按地图反查。

    ⚠️ 上一版靠 `--maps` 传图号、按那几张图的剧情战斗反查敌人。不给参数就
    反查到 **0 个遇敌群 → 0 个敌人素材**，而且只打印一行、不报错。后果是
    走到没导过的那张图触发战斗，`BattleScene.buildUnits` 把站立素材没预载的
    单位**静默丢掉** —— 画面上「对面是空的，待一会儿自动结束」。
    用户 2026-09-19 在肅州城门口撞上。

    这正是判据表 F 组那条的翻版：**一张图需要额外保留什么，要写进工具里的表，
    不许靠命令行参数**（`--also-objects` 那次一模一样）。所以这里改成拿
    `gamedata.敌人` 全表求集合 —— 138 条，走到哪打到哪都不会缺。
    """
    arts = {rec.get("素材") for rec in (gamedata.get("敌人") or {}).values()}
    return {str(a) for a in arts if a}


#: **前端代码直接点名、数据里没人引用**的包。按数据反查漏掉它们，干净环境重导就缺：
#: `FLR000` 是 `encounter.FALLBACK_FLOOR`（每场入场都备、查不到背景时用，BattleScene 选格底图也取它），
#: 2026-09-28 一键提取实测缺它则每场战斗都进不去。
CODE_REFERENCED = ("FLR000",)


def wanted_packs(gamedata: dict, maps_dir: Path, maps: list[str],
                 fight: Path, skills_path: Path | None = None) -> set[str]:
    swarms = script_swarms(maps_dir, maps)
    groups = groups_of(gamedata, swarms)
    # ⭐ 敌人素材与战斗背景**一律全导**。
    # ⚠️ 背景原先也走 `--maps` 反查，于是全库 61 张只导出 8 张 ——
    # 打到没导过的那一场就退回 `FALLBACK_FLOOR`，画面是**另一张地面**，
    # 而且不报错。与敌人素材同一个病根（判据表 F 组「不许靠命令行参数」）。
    arts = all_foe_arts(gamedata)
    all_swarms = {int(s["编号"]) for s in gamedata.get("遇敌群", []) if "编号" in s}
    floors = floor_packs(gamedata, all_swarms)
    print(f"敌人素材 {len(arts)} 个、战斗背景 {len(floors)} 张（全表导，不按地图反查）；"
          f"参考：这些图的剧情战斗用到 {len(swarms)} 个遇敌群 → {len(groups)} 个遇敌组")
    packs = {f"{kind}{art}" for art in PARTY_ART.values() for kind in PARTY_KINDS}
    packs |= {f"{kind}{art}" for art in arts for kind in FOE_KINDS}
    packs |= floors
    packs |= set(CODE_REFERENCED)
    packs |= ui_packs(fight)
    if skills_path is not None:
        actions = skill_action_packs(skills_path)
        print(f"绝学自己点名的包 {len(actions)} 个"
              "（蓄劲/移动/攻击/失误/归位 + 五槽动画）")
        packs |= actions
    return packs


def refresh_motion(fight: Path, out: Path) -> int:
    """只重建移动/归位的头部参数；图片、图集、帧和音效保持原产物。"""
    import sf2
    import sf2_anim
    from export_map import load_patch
    count = 0
    for kind in ('MOV', 'BAK'):
        archive = fight / ARCHIVES[kind]
        patch = load_patch(archive)
        patch.update(load_patch(fight / f'{kind}.DAT'))
        with archive.open('rb') as fh:
            entries = {e.name.upper(): e for e in dat_unpack.read_entries(fh, archive.stat().st_size)}
            for folder in sorted(out.glob(f'{kind}*')):
                path = folder / 'anim.json'
                if not path.is_file():
                    continue
                name = folder.name + '.SF2'
                raw = patch.get(name)
                if raw is None:
                    entry = entries[name]
                    fh.seek(entry.offset)
                    raw = fh.read(entry.size)
                header = sf2.parse_header(sf2.inflate_variant(raw))
                doc = json.loads(path.read_text())
                doc['motion'] = sf2_anim.to_dict(header, [])['motion']
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
                count += 1
    return count


def refresh_camera(fight: Path, out: Path) -> int:
    """保留图片/图集，按原始SF2补帧上的镜头跟随/放大/回中字段（BTL-29，只写非零项）。"""
    import sf2
    import sf2_anim
    from export_map import load_patch
    count = 0
    for kind, archive_name in ARCHIVES.items():
        if kind in ('FLR', 'ITF'):
            continue
        archive = fight / archive_name
        patch = load_patch(archive)
        patch.update(load_patch(fight / f'{kind}.DAT'))
        with archive.open('rb') as fh:
            entries = {e.name.upper(): e for e in dat_unpack.read_entries(fh, archive.stat().st_size)}
            for folder in sorted(out.glob(f'{kind}*')):
                path = folder / 'anim.json'
                name = folder.name + '.SF2'
                if not path.is_file() or (name not in patch and name not in entries):
                    continue
                raw = patch.get(name)
                if raw is None:
                    entry = entries[name]
                    fh.seek(entry.offset)
                    raw = fh.read(entry.size)
                raw = sf2.inflate_variant(raw)
                fresh = sf2_anim.to_dict(sf2.parse_header(raw), sf2_anim.parse_frames(raw, sf2.parse_header(raw)))['frames']
                doc = json.loads(path.read_text())
                before = json.dumps(doc, ensure_ascii=False)
                frames = []
                for old, new in zip(doc['frames'], fresh):
                    # 键序与完整导出一致（镜头字段在layers之前），再跑一键提取才能逐字节相同。
                    merged = {k: v for k, v in old.items() if k != 'layers' and k not in sf2_anim.SPARSE_CAMERA_FIELDS}
                    merged.update({k: new[k] for k in sf2_anim.SPARSE_CAMERA_FIELDS if k in new})
                    merged['layers'] = old['layers']
                    frames.append(merged)
                doc['frames'] = frames + doc['frames'][len(frames):]
                if json.dumps(doc, ensure_ascii=False) != before:
                    # 与export_pack相同：不带末尾换行。
                    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
                    count += 1
    return count


def refresh_reactions(fight: Path, out: Path) -> int:
    """保留图片/图集，补原始图片记录+12的外借HIT帧；含SHO移动头。"""
    import sf2
    import sf2_anim
    from export_map import load_patch
    count = 0
    for kind in ('ATT', 'MIS', 'EFF', 'SHO'):
        archive = fight / ARCHIVES[kind]
        patch = load_patch(archive)
        patch.update(load_patch(fight / f'{kind}.DAT'))
        with archive.open('rb') as fh:
            entries = {e.name.upper(): e for e in dat_unpack.read_entries(fh, archive.stat().st_size)}
            for folder in sorted(out.glob(f'{kind}*')):
                path = folder / 'anim.json'
                name = folder.name + '.SF2'
                if not path.is_file() or (name not in patch and name not in entries):
                    continue
                raw = patch.get(name)
                if raw is None:
                    entry = entries[name]
                    fh.seek(entry.offset)
                    raw = fh.read(entry.size)
                raw = sf2.inflate_variant(raw)
                header = sf2.parse_header(raw)
                images = {i.index: i for i in sf2.read_images(raw, header)}
                doc = json.loads(path.read_text())
                before = json.dumps(doc, ensure_ascii=False)
                for image in doc['images']:
                    original = images.get(image['index'])
                    if original and original.borrowed_hit_frame:
                        image['borrowed_hit_frame'] = original.borrowed_hit_frame
                if kind == 'SHO':
                    doc['motion'] = sf2_anim.to_dict(header, [])['motion']
                if json.dumps(doc, ensure_ascii=False) != before:
                    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n')
                    count += 1
    return count

def run_export_pack(out_path, pack_list):
    return subprocess.run(
        [sys.executable, str(Path(__file__).with_name("export_pack.py")),
         str(out_path), *pack_list], check=False).returncode

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fight_dir", help="multimedia/fight")
    parser.add_argument("out_dir", help="game/public/assets")
    parser.add_argument("--gamedata", required=True)
    parser.add_argument("--maps-dir", required=True)
    parser.add_argument("--maps", default="",
                        help="逗号分隔的图号，仅用于剧情战斗诊断参考")
    parser.add_argument("--references-only", action="store_true",
                        help="仅构建绝学/物品全表明确引用；不构建按角色编号推导的默认包")
    parser.add_argument("--check", action="store_true", help="只检查绝学/物品引用闭合，不修改素材")
    parser.add_argument("--report", type=Path, help="写出引用来源和缺失资源报告")
    parser.add_argument("--source-manifest", type=Path,
                        help="官方源盘点；用于显式报告已有源缺口，不掩盖新增漏导")
    parser.add_argument("--scan-sources", type=Path,
                        help="只读扫描官方fight目录，输出来源指纹与缺口后退出")
    parser.add_argument("--strict", action="store_true", help="已登记源缺口也返回失败")
    parser.add_argument('--refresh-motion', action='store_true', help='从原始SF2重建已有MOV/BAK的移动参数，不重导图片')
    parser.add_argument('--refresh-reactions', action='store_true', help='补外借HIT姿势及飞行素材移动参数')
    parser.add_argument('--refresh-camera', action='store_true', help='补帧上的镜头跟随/放大/回中字段，不重导图片')
    parser.add_argument("--force", action="store_true",
                        help="**改了导出判据之后要跑这个**：连同 out_dir 里已经导过的"
                             "同类包一起重导。默认是幂等跳过，于是判据改了也不生效 —— "
                             "`stand_point` 的算法换过一次，就是卡在这儿没重导成")
    args = parser.parse_args(argv)

    gamedata = json.loads(Path(args.gamedata).read_text(encoding="utf-8"))
    maps = [m.strip().upper() for m in args.maps.split(",") if m.strip()]
    fight = Path(args.fight_dir)
    skills = Path(args.gamedata).with_name("skills.json")
    refs = reference_packs(skills.parent)
    if args.scan_sources:
        manifest = source_inventory(fight, refs)
        args.scan_sources.parent.mkdir(parents=True, exist_ok=True)
        args.scan_sources.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(f"源扫描：{len(refs)}引用，{len(manifest['sources'])}可定位，{len(manifest['sourceGaps'])}缺口")
        return 0
    packs = set(refs)
    if not args.references_only and not args.check and not args.refresh_motion and not args.refresh_reactions and not args.refresh_camera:
        packs |= wanted_packs(gamedata, Path(args.maps_dir), maps, fight, skills)

    out = Path(args.out_dir)
    if args.refresh_reactions:
        print(f'重建{refresh_reactions(fight, out)}个受击/飞行参数')
        return 0
    if args.refresh_camera:
        print(f'补{refresh_camera(fight, out)}个包的镜头字段')
        return 0
    manifest_path = args.source_manifest or skills.with_name("battle_sources.json")
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    if args.refresh_motion:
        print(f'重建{refresh_motion(fight, out)}个移动/归位参数')
        return 0
    if args.check:
        result = check_references(out, refs, manifest)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(f"明确引用{len(refs)}包，未解决源缺口{len(result['sourceGaps'])}包，新增缺失/不完整{len(result['failures'])}包")
        for name in result['sourceGaps']:
            print(f"WARN SOURCE_GAP {name}: {refs[name]}")
        return int(bool(result["failures"] or (args.strict and result['sourceGaps'])))
    staged, missing = [], []
    # 与地图生成链共用同名散装覆盖规则，补丁新增包也纳入索引。
    from export_map import load_patch
    with tempfile.TemporaryDirectory() as tmp:
        for kind, filename in ARCHIVES.items():
            archive = fight / filename
            want = {p for p in packs if p.startswith(kind)}
            if args.force and out.is_dir():
                # 已经在产物目录里的同类包也一并刷新 —— 判据变了就得让它们重算
                want |= {d.name for d in out.iterdir()
                         if d.is_dir() and d.name.startswith(kind)}
            if not want:
                continue
            patch = load_patch(archive)
            # fight 使用 eff/att/...，地图通常使用与DAT同名的目录。
            patch.update(load_patch(fight / f"{kind}.DAT"))
            if not archive.is_file():
                for name in sorted(want):
                    raw = patch.get(f"{name}.SF2")
                    if raw is None:
                        missing.append(name)
                    elif args.force or pack_errors(out, name):
                        dst = Path(tmp) / f"{name}.SF2"
                        dst.write_bytes(raw)
                        staged.append(str(dst))
                continue
            with archive.open("rb") as fh:
                entries = dat_unpack.read_entries(fh, archive.stat().st_size)
                by_stem = {e.name.rsplit(".", 1)[0].upper(): e for e in entries}
                for name in sorted(want):
                    entry = by_stem.get(name)
                    raw = patch.get(f"{name}.SF2")
                    if entry is None and raw is None:
                        missing.append(name)
                        continue
                    if not args.force and not pack_errors(out, name):
                        continue                       # 已经导过，幂等
                    if raw is None:
                        fh.seek(entry.offset)
                        raw = fh.read(entry.size)
                    dst = Path(tmp) / f"{name}.SF2"
                    dst.write_bytes(raw)
                    staged.append(str(dst))

        if not staged:
            print("没有要新导的包（都已存在）")
        else:
            print(f"要导 {len(staged)} 个包…")
            # fix [WinError 206] 文件名或扩展名太长
            if platform.system() == "Windows":
                batch_size = 100
                for i in range(0, len(staged), batch_size):
                    batch = staged[i:i + batch_size]
                    print(f"处理批次 {i//batch_size + 1}, {len(batch)} 个包")
                    rc = run_export_pack(out, batch)
                    if rc != 0:
                        return rc
            else:
                rc = run_export_pack(out, staged)
                if rc != 0:
                    return rc
                    

    if missing:
        # ⚠️ **缺包不一定是错**：`0040`/`0060` 本来就没有 MOV。
        # 但要打印出来，否则又变成"静默少一个人"。
        print(f"对应归档/同名散装中未找到的包（{len(missing)} 个，原因须另核）："
              + "、".join(sorted(missing)))
    result = check_references(out, refs, manifest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"明确引用{len(refs)}包，未解决源缺口{len(result['sourceGaps'])}包，新增缺失/不完整{len(result['failures'])}包")
    return int(bool(result["failures"] or (args.strict and result['sourceGaps'])))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

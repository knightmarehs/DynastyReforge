/** 人物派生数值、成长辅助与绝学习得条件。
 * 战斗伤害在officialDamage/combat/skills，状态在battleStates，战后成长在spoils。
 * 版本化系数来自gamedata；五内抗性仍有历史反推依据，不应称全部公式均已核原程序。
 * 数据来源和复用边界见docs/专题/数值体系.md。
 */

/** 八项抗性的固定顺序。界面从上到下就是这个序。 */
export const RESISTS = Object.freeze([
  '焚火', '冰凛', '雷荧', '烨光', '魔厉', '化相', '析魂', '外法',
]);

/** 五内（内在，五内页那五块牌）。 */
export const WUNEI = Object.freeze(['迅', '烈', '神', '魔', '魂']);

/**
 * 五外（外在，状态页那五根柱子）。
 * ⚠️ **「五外」是原作叫法**，与「五内」对仗 —— 别叫「五柱」。
 * 绝学资料里状态持续公式的「目标相关五外」指的就是这五项。
 */
export const WUWAI = Object.freeze(['膂力', '灵力', '体魄', '迅捷', '机运']);

/**
 * 版本 → 该用哪张五内抗性系数表。
 *
 * ⚠️ **两张表不通用，用错版本八项全错**（各自 8/8 验证过）。
 * 曾经这里硬编码成 `'本补丁'`，切到官方版数据后就整个错了 —— 所以现在
 * **跟着 `gamedata.版本` 走**，那个字段由 `export_gamedata.py` 写入。
 */
export const RESIST_TABLE_BY_VERSION = Object.freeze({ 官方: '原版', '300块': '本补丁' });

/**
 * 取当前版本的五内抗性系数表。
 * @param {object} gamedata
 * @returns {object} {迅: {焚火: n, …}, …}
 */
export function resistTable(gamedata) {
  const name = RESIST_TABLE_BY_VERSION[gamedata?.版本];
  if (!name) throw new Error(`gamedata.版本 认不出来：${gamedata?.版本}`);
  const table = gamedata?.五内抗性系数?.[name];
  if (!table) throw new Error(`gamedata 里没有「${name}」这张五内抗性系数表`);
  return table;
}

/** 向下取整。原作这些派生值全是截断，不是四舍五入（实测两个版本共 5 个采样点）。 */
const floor = Math.floor;

// ───────────────────────────── 位阶与成长 ─────────────────────────────

/**
 * 已有历练 → 位阶。
 *
 * **【资料 + 实测】** 门槛表来自 `public/Levelup.enc`（101 档累计历练）。
 * ⚠️ **门槛表是 0 基的**：`表[0] = 0` 对应**位阶 1**，所以 `位阶 = 下标 + 1`。
 * 实测：300 块位阶2 时显示「历练 四」，而表是 `[0, 4, 10, …]` —— 第 4 点（下标1）
 * 正好跨过门槛，对应位阶 2。
 *
 * 只用于缺少位阶的模板回退；有存档位阶时不得由历练反推覆盖。
 * 原作界面的「历练」显示剩余门槛，见experienceRemaining，不是这个累计值。
 *
 * @param {number} exp 已有历练（累计）
 * @param {number[]} thresholds gamedata.历练门槛
 */
export function rankFromExp(exp, thresholds) {
  // ⚠️ 门槛表缺了要出声。静默返回 1 的话，全队等级会齐刷刷变成 1 级，
  // 而那看起来像「存档读坏了」，查起来会绕到存档那边去。
  if (!Array.isArray(thresholds) || !thresholds.length) {
    console.warn('rankFromExp: 缺少 gamedata.历练门槛，位阶按 1 算');
    return 1;
  }
  let index = 0;
  for (let i = 0; i < thresholds.length; i += 1) {
    if (exp >= thresholds[i]) index = i; else break;
  }
  return index + 1;
}

/** 0x443cf3–0x443d0e：界面历练=下一阶累计门槛−已有累计历练，负值显示0。 */
export function experienceRemaining(member, thresholds) {
  const goal = thresholds?.[member?.位阶];
  return Number.isFinite(goal) ? Math.max(0, goal - (Number(member?.已有历练) || 0)) : 0;
}

/**
 * 升一级时某项五外涨多少。
 *
 * **【资料 + 实测】** 配套资料给的是**区间均值**，实际每级在区间内随机。
 * 判据：200 块有一份「最大成长」补丁的存档，位阶 5/19/24 三点**完全线性**
 * （膂力+5 灵力+7 体魄+6 迅捷+7 机运+6 每级），正是 `[⌊均值⌋−1, ⌈均值⌉+1]` 的上界；
 * 300 块位阶1→2 的单次实测也落在区间内。见 `docs/专题/数值体系.md` §3.1。
 *
 * @param {[number, number]} range gamedata.成长[角色].range[项]
 * @param {() => number} rand 0~1 随机源，默认取**区间上界**（等价于最大成长补丁）
 */
export function growWuwai(range, rand = null) {
  const [lo, hi] = range;
  if (!rand) return hi;
  return lo + floor(rand() * (hi - lo + 1));
}

/**
 * 升一级时命极 / 气极涨多少。
 *
 * **【资料】** `2－用器资料.txt` 开头两行：
 * ```
 * 命极成长 ＝ (体魄 − 级别)/10 + 4 + 0~2
 * 气极成长 ＝ (灵力×2/3 − 级别)/25 + 3 + 0~2
 * ```
 * **【实测坐实】** 从 200 块位阶5 连推 19 级到位阶24，命 443/539、气 172/214
 * 四个数全中、零误差。同时确认了三件事：
 * 「级别」取**升级后**的值、结果**向下取整**、随机项上界为 2。
 *
 * @param {number} bodyOrSpirit 命极用体魄，气极用灵力
 * @param {number} newRank 升级**后**的位阶
 * @param {object} k gamedata.常数
 * @param {number} extra 随机项 0~2，默认取满
 */
export function growHpMax(体魄, newRank, k, extra = k.成长随机上限) {
  return floor((体魄 - newRank) / k.命极成长体魄除数 + k.命极成长基数 + extra);
}

export function growQiMax(灵力, newRank, k, extra = k.成长随机上限) {
  return floor((灵力 * k.气极灵力系数 - newRank) / k.气极成长除数 + k.气极成长基数 + extra);
}

// ───────────────────────────── 抗性 ─────────────────────────────

/**
 * 内禀抗性（**五内页**显示的那一套，管状态持续时间）。
 *
 * **【资料 + 实测】** `内禀 = 基准100 + Σ(五内点数 × 系数)`。
 * 系数表来自配套资料「6－五魂化蕴」，**两个版本各一张**。
 * 两版各 8/8 验证：300 块存档只有「本补丁」表对，200 块只有「原版」表对。
 *
 * @param {object} wunei {迅,烈,神,魔,魂}
 * @param {object} table gamedata.五内抗性系数[版本]
 * @param {object} k gamedata.常数
 * @returns {object} {焚火: n, ...}
 */
export function innateResist(wunei, table, k) {
  const out = {};
  for (const name of RESISTS) {
    let v = k.抗性基准;
    for (const w of WUNEI) v += (wunei[w] ?? 0) * (table[w]?.[name] ?? 0);
    out[name] = v;
  }
  return out;
}

/**
 * 及身抗性（**及身页**显示的那一套，管伤害与特效命中）。
 *
 * **【实测】** 装备对抗性是**乘性**不是加性：
 * ```
 * 及身抗性 = ⌊ 内禀 × Π(每件装备的该项抗性 / 100) ⌋
 * ```
 * **空白 = 100 → 系数 1.0 → 不起作用** —— 配套 xlsx「抗性空白值为１００」
 * 说的就是这个默认**乘数**，不是默认抗性。
 *
 * 三组装备验证：无抗性装备时两页读数完全相同；辟邪玉佩那组三项系数区间都含 0.80；
 * 凛日神刀那组冰凛恰好 2.00、雷荧与烨光恰好 0.80。见 `docs/专题/数值体系.md` §3.3bis。
 *
 * @param {object} innate innateResist 的结果
 * @param {object[]} gear 已装备的物品（equipment.json 的记录），null 会被跳过
 */
export function gearResist(innate, gear) {
  const out = {};
  for (const name of RESISTS) {
    let factor = 1;
    for (const item of gear) {
      const v = item?.[name];
      if (typeof v === 'number') factor *= v / 100;
    }
    out[name] = floor(innate[name] * factor);
  }
  return out;
}

// ───────────────────────────── 诸能四值 ─────────────────────────────

/** 把三个装备槽里某个补正字段加起来。空槽与空字段都当 0。 */
export function gearBonus(gear, field) {
  return gear.reduce((sum, item) => {
    const v = item?.[field];
    return sum + (typeof v === 'number' ? v : 0);
  }, 0);
}

/**
 * 诸能四值：攻击 / 护禦 / 命中 / 闪避。
 *
 * **【资料】闪避**（`5－绝学资料.txt`【名词解释】原文）：
 * ```
 * 目标闪避 ＝ 目标装备闪避补正 + 目标机运/4
 * 普攻命中概率百分数 ＝ 攻方命中 − 目标闪避
 * ```
 *
 * ⭐ **命中不走资料那条。** 资料写的是 `90 + 装备命中补正 + 机运/4`，
 * 而原作显示的是 **`100 + 装备命中补正`，机运不参与** —— 9 个采样全中
 * （`Save008` 五人机运 170/165/80/234/125、`Save009` 霍雍机运 400、
 * 200 块三张机运 74/158/188），每一个的「显示值 − 装备补正」都正好是 100。
 * 判据与边界写在 `tools/export_gamedata.CONSTANTS`。
 * **【实测】攻击与护禦**（资料里没写）：
 * ```
 * 攻击 ＝ 膂力       + 装备攻击补正
 * 护禦 ＝ ⌊体魄/2⌋   + 装备防御补正
 * ```
 * 判据：200 块三张同装备不同等级（位阶 5/19/24）的状态页，
 * 三点反解出的装备补正完全一致（攻+1300 防+1200 闪−10），
 * 而防御那个 1200 恰好等于 300 块 `equipment.json` 里幽日神袍的「防御补正」。
 *
 * ⚠️ **曾经在这里编过一个「命中上限 130」。** 来源是「三张同装备不同机运
 * 恒为 130」—— 我把「没变化」读成了"撞上限"，而真相是**机运本来就不影响命中**。
 * 那个编出来的 130 一遇到霍雍（装备补正 60、原作显示 160）就露馅。
 * **一个量不随输入变化时，先想"它是不是根本不吃这个输入"，再想截断。**
 *
 * @param {object} stats 五外 {膂力,体魄,迅捷,机运,...}
 * @param {object[]} gear 三个装备槽
 * @param {object} k gamedata.常数
 */
export function derivedStats(stats, gear, k) {
  return {
    攻击: (stats.膂力 ?? 0) + gearBonus(gear, '攻击补正'),
    护禦: floor((stats.体魄 ?? 0) / k.护禦体魄除数) + gearBonus(gear, '防御补正'),
    // **机运不参与** —— 见上面那段。别再把 `机运/4` 加回来。
    命中: k.命中基数 + gearBonus(gear, '命中补正'),
    闪避: floor((stats.机运 ?? 0) / k.闪避机运除数) + gearBonus(gear, '闪避补正'),
  };
}

// ───────────────────────────── 命极 / 气极 ─────────────────────────────

/**
 * 装备对命极 / 气极的补正。
 *
 * > ⚠️ **这两项是「换装时一次性增减」，不是动态叠加。**
 * > 配套 xlsx「概念释例」原文：「**当且仅当**在游戏及身界面更换装备时，
 * > 系统根据装备命、气极补正对角色的命、气极进行增减计算。」
 * > 也就是说它**永久改写角色的命极**，脱下时再减回去。
 * > 按「每次读取时叠加」实现会得到不同结果 —— 所以这里给的是**换装时调用一次**的差值，
 * > 不要放进 `derivedStats` 那种每帧都可能调用的地方。
 *
 * @param {object|null} oldItem 换下来的
 * @param {object|null} newItem 换上去的
 * @returns {{命极: number, 气极: number}} 应当施加到角色身上的增量
 */
export function equipMaxDelta(oldItem, newItem) {
  const pick = (item, field) => (typeof item?.[field] === 'number' ? item[field] : 0);
  return {
    命极: pick(newItem, '命极补正') - pick(oldItem, '命极补正'),
    气极: pick(newItem, '气极补正') - pick(oldItem, '气极补正'),
  };
}

// ───────────────────────────── 绝学习得 ─────────────────────────────

/**
 * 某角色在当前位阶与五内下能学会哪些绝学。
 *
 * **【资料 + 二进制】** 条件表来自 `public/Magictb.enc`（133 条 × 9 u32），
 * 与配套资料「6－五魂化蕴」的 136 条逐条吻合，绝学代码对 `skills.json` 命中 132/133。
 *
 * **「级别A」＝**最低习得位阶**（需五内全部达标）；
 * 「级别B」＝**保底强制习得位阶**（**无视五内**；0 表示没有保底）。判据：
 * 首条 `[1,10,20,…]` ＝夏侯仪「摄魂鬼爪」（需魔20魂10）——位阶 10 且五内达标即学、
 * 否则位阶 20 强制学会（B>A，正是「保底」该有的形状）。
 *
 * @param {number} code 战斗角色代码
 * @param {number} rank 位阶
 * @param {object} wunei {迅,烈,神,魔,魂}
 * @param {object[]} table gamedata.绝学习得
 */
export function learnableSkills(code, rank, wunei, table) {
  return table.filter((row) => row.角色代码 === code
    && ((rank >= row.级别A && WUNEI.every((w) => (wunei[w] ?? 0) >= (row.五内[w] ?? 0)))
      || (row.级别B > 0 && rank >= row.级别B)));
}

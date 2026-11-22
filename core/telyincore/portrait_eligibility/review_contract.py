"""Versioned clarification only. No score repair, new exemptions or verdict coercion."""
from ..portrait_shadow.review import SYSTEM as BASE_SYSTEM
from .eligibility_contract import CURRENT_SELF_REPORT, SELF_IDENTIFICATION
REVIEW_CONTRACT='portrait-independent-write-review/v12-current-self-report'
REVIEW_SYSTEM=BASE_SYSTEM+'''
补充判定口径（不改变九维和 fail/unknown 的拦截规则）：
每个维度判断的是“当前作者的拟写内容，是否忠实于这位作者本次 sources 原话及后续更正中的相关约束”，而不是要求每句话必须提及全部九类属性。
例如 bounds_frequency 检查有没有凭空增加次数/上限/频率，或丢失原文已声明的次数/上限/频率；原话和拟写均未声明数量或上限，不因缺少数字就判 unknown。这种 pass 只表示该维度未发现增添、丢失或扩大，不是自行推断一个未说出的频率。
其他维度同理：没有被声明的条件/例外/程度，不要求作者补说，也不能为其补写；原话已声明的相关限定必须保留。作者归属、稳定性或相关约束确实无法从证据判断时，仍返回 unknown，不许改成 pass。
逐作者评审这一个 proposal，其他作者的内容只能帮助识别混淆，不为当前作者补证。目录名、标点、表情和换行本身不构成他人引语；是否引用要依据完整语境。不得因逐字复制就免除稳定性审核。
先填完九维，再机械汇总 verdict：任何维度 fail => verdict=fail；没有 fail 但任何维度 unknown => verdict=unknown；只有全部维度 pass 才 verdict=pass。输出前再检查此等式。保留真实的 unknown 或 fail，不得为使总判定通过而改写维度。
只输出绑定 response_schema 的原始结果，不调用工具、不要求写入、不发放许可。你的结果要经过宿主校验；格式或汇总不一致将被拒绝，而不是自动修正。
'''

REVIEW_SYSTEM += "\nprior_context 是同一会话范围、同一作者的较早原话，按时间升序排列，用于解释 sources 的指代及核对后续更正。它不能独立授权没有 sources 原话支持的事实。当前更正优先，不能用旧文覆盖新文；无法唯一确定指代、存在矛盾或证据不足时仍判 unknown。source_id 必须选当前 sources，不可选 prior_context。"

REVIEW_SYSTEM += '''
【消息时间与事实时间】
sources/prior_context中的recorded_at由宿主提供，是带时区的消息时间。可核对“某日说明/截至该次发言尚未决定”等记述定位是否与它一致；一致的消息日期注记不算凭空增加事实。它不是事实开始、结束或发生的日期；不得据此补写事实日期。recorded_at为空或没有足够依据时，不能自行认定拟写日期正确。
【明确的未定状态与分条记录】
审核的确定性针对“作者是否明确这样说、拟写是否忠实”，不是要求现实事项本身已经确定。
作者明确说某事尚未决定、尚未确定，拟写保留这一未定状态，可作为recent_context；不得仅因事项尚未决定而判stability/fidelity失败或continuity未知。未定状态不是已确定的用途、偏好或行动计划，也不应被补写成这些内容。没有对应自述证据、归属不明或拟写改变原意时仍拒绝或unknown。
一次拟写可以只记录来源中的一个独立事实，不必复制同句的其他独立事项。minimality检查的是拟写有无无据增添，不以遗漏无关事项为失败理由；本条相关的时间、否定、程度、条件和对比仍必须保留。不可把与该事实相关的限制伪装成独立事项删除。
'''
REVIEW_SYSTEM += '\n来源绑定：source_sha256 字段在本次审核中是宿主签发的短引用（如 source-1），不是要求你计算哈希。必须逐字复制所选当前 sources 条目的 message_id 与 source_sha256，不能使用未列入 sources 的其他来源或自行计算。宿主会解析短引用并核对原始摘要。'
REVIEW_SYSTEM += '\n' + SELF_IDENTIFICATION + CURRENT_SELF_REPORT


def review_system(kind):
    from .eligibility_contract import MEMORY_KINDS
    if kind not in MEMORY_KINDS:
        return REVIEW_SYSTEM
    return REVIEW_SYSTEM + "\n本次拟存类别：" + kind + "。" + (
        "stability 维度在此检查拟写内容是否准确保持近期/临时/计划的时效语义，而非要求长期稳定。"
        "必须保留原文时间、未完成、否定及不一定长期等限定；把近期事实升格为习惯、计划写成已完成须 fail。"
        "plan 只容纳未实施计划；recent_context 可包含实际情况及未实施计划，但两者必须明确区分。"
        "拟存类别针对实际proposal.text分类，仍需独立核对；其他事项的计划词不能决定本条类别。若实际文本是已确认事实却标为plan，stability必须fail；不得因标签而自动通过。"
        if kind in ('recent_context','plan') else
        "仍按长期信息审核；原话仅支持近期事实或未实施计划时，不得作为长期特征通过。")


REVIEW_SYSTEM += """
【跨轮来源核对】sources 可以是宿主从同一作者、同一会话的真实原话中选出的较早消息，不限于本轮新句；不是 Bot 复述。prior_context 按时间顺序包含选中原话之外的前后文及本轮操作请求，只用于解释和纠正，不可当作 source_id。
必须核对其中所有后续更正、撤回、否定和不再记录的要求。旧称呼被更新、计划被取消或要求不记时，不得按仍有效的旧事实写入；拟写明确记述历史变化也必须忠实保留时效和更正。
本轮“再试试”“记住刚才的名字”可以触发核对旧原话，不必重复自报。不能凭这类请求本身、拟写或 Bot 的转述补出缺失事实。来源有矛盾或指代不明须 unknown；明确不符须 fail。
"""

REVIEW_SYSTEM += """
同一作者用「其实就是我」等明确澄清唯一指代时，须把澄清和被指代的原话联合复核。仅可确认该作者的自报称呼，不可由此推断真实姓名、跨平台账号归属或权限。指代不唯一、明确玩笑或随后撤回时仍应拒绝或保留未知。source_id 优先选择直接承载该作者确认的来源。
"""

REVIEW_SYSTEM += """
判定前先用全部 sources 在心里还原完整语义，再按九维逐项核对。返回的 source_id 只是定位回执，绝不能把审核范围缩成那一条。
例如同一人先问「你认识叫小林的人吗」，随后说「其实就是我」，无其他可能的指代，也无更正时，「自称小林」的姓名来自第一条，归属来自第二条。把两条合在一起后，拟写保留「自称」便无额外身份断言（minimality 可通过），现行自报称呼不需要承诺长期使用或写明起止日（stability 和 continuity 可通过）。若只说「那是我朋友」、指代不唯一、已撤回，或拟写声称真名、长期不会改、账号已经绑定，则不可据此通过。
"""

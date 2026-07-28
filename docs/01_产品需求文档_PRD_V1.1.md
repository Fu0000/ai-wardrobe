# AI Wardrobe 产品需求文档 PRD V1.1

## 一、产品概述
产品形态：微信小程序优先。
产品定位：Personal Style Agent。
目标：长期降低用户穿搭与服装消费决策成本。

AI Wardrobe 不是单纯电子衣橱、AI 试衣工具或图片生成器，而是逐步理解用户本人、真实衣橱、穿着行为和生活场景的个人 AI 穿搭顾问。

## 二、核心目标用户
第一阶段：18～30 岁年轻用户，优先大学生、初入职场、年轻女性和有穿搭需求但缺少系统方法的人群。
封闭测试：30～50 人。
第二阶段：300～1000 人。

## 三、核心产品假设
1. 一张照片即可产生足够强的即时价值。
2. Minimal Change Before/After 比完全重做更可信、更易传播。
3. Wow Moment 后用户更愿意渐进建立数字衣橱。
4. 用户愿意持续让 AI 使用自己的真实衣服做决策。
5. 真实穿着行为比口头偏好更有价值。
6. 长期订阅价值来自天气、场景、状态和旧衣新搭，而不是永久“探索风格”。
7. 商品链接快速入库能降低建库成本并提高数据质量。
8. 可视化数字衣橱能增强资产感和分享动力。
9. Existing Wardrobe First 能提高推荐可信度。

## 四、一级信息架构
TabBar：
- 今日
- 衣橱
- 穿搭
- 我的

### 今日
AI 穿搭诊断、AI 帮我搭、从衣橱选、最近 Look、天气与场景。

### 衣橱
Collection Mode：Visual Shoe Wall / Visual Wardrobe / Reality View / AI View。
Management Mode：搜索、分类、筛选、标签、状态、批量管理。

Management Mode 能力矩阵：

| 能力 | P0.5 | P1 | P1.5 |
|:---|:---:|:---:|:---:|
| 名称搜索 | ✓ | ✓ | ✓ |
| Alias 搜索 | ✓ | ✓ | ✓ |
| 类目筛选 | ✓ | ✓ | ✓ |
| 颜色筛选 | ✓ | ✓ | ✓ |
| 状态筛选 | ✓ | ✓ | ✓ |
| 材质 / 版型 | | ✓ | ✓ |
| 场景筛选 | | ✓ | ✓ |
| 最近穿着 | | ✓ | ✓ |
| Essential 排序 | | ✓ | ✓ |
| 批量删除 | ✓ | ✓ | ✓ |
| 批量修改分类 | ✓ | ✓ | ✓ |
| 批量待洗 | | ✓ | ✓ |
| 批量移动 Scene | | | ✓ |
| AI 批量整理 | | | ✓ |

默认排序：Collection Mode 按用户空间位置，Management Mode 按最近更新。
用户可切换：最近录入、最近穿过、最常穿、核心程度、长期闲置。

### 穿搭
AI Outfit、我的 Look、穿搭日历、最近穿过、收藏、周计划。

### 我的
用户资料、Style Profile、生成记录、分享记录、隐私数据、会员。

## 五、首次用户旅程
进入小程序
→ 上传穿搭照片
→ 选择场景
→ AI 穿搭诊断
→ 得到评分、优点、Primary Issue
→ “看看优化后是什么样”
→ Minimal Change Optimization
→ Before/After
→ 明确“只改了什么”
→ 分享
→ 引导录入真实衣物。

## 五点五、产品性能体验目标（SLO）
核心原则：用户不一定必须在 30 秒拿到最终图片，但必须在 3 秒内知道系统已经开始工作，在 15～20 秒内获得第一份价值（文字诊断先行）。

| 阶段 | P50 目标 | P90 目标 | 超时体验 |
|:---|---:|---:|:---|
| 照片上传完成后进入分析 | ≤3 秒 | ≤5 秒 | 明确进入任务状态 |
| AI 诊断（文字结果） | ≤10 秒 | ≤20 秒 | 超过 8 秒显示阶段进度 |
| Optimization 图片 | ≤30 秒 | ≤60 秒 | 用户可以离开页面 |
| 完整 Before/After | ≤45 秒 | ≤75 秒 | 后台继续处理 |

渐进展示路径：0～3 秒上传完成 → 5～15 秒先展示文字诊断 → 15～60 秒继续生成 After 图片。
以上为 P0 目标 SLO（非绝对承诺），通过真实压测和 30～50 人测试校准后定稿。

## 五点六、Network Resilience UX

### Level 1：弱网可恢复（P0）
上传开始 → 保存 Local Upload Draft → 网络断开标记 INTERRUPTED → 网络恢复后用户继续上传 / 重试。
P0 不做真正分片断点续传，上传失败保留草稿，用户手动重新上传。

### Level 2：任务状态可恢复（P0）
jobId 本地持久化。用户关闭页面后重新打开，自动恢复查询未完成 Job。前端维护 pendingJobs[] 列表。

### Level 3：衣橱离线只读（P0.5/P1）
本地保存最近一次 Scene Snapshot 本地文件 + Scene Metadata + Hotspot Metadata。
离线时提示："当前展示的是你最后一次同步的衣橱。"不追求完整离线数据库。

## 六、Minimal Change Optimization
原则：达到明显改善所需要的最少修改。
Change Budget：
- Level 1：调整穿法
- Level 2：替换 1 件
- Level 3：最多替换 2 件

Garment Resolver 顺序：
1. Own Wardrobe
2. Commerce Product
3. Generated Generic Garment

## 七、Universal Wardrobe Ingestion
入口：
- 生活照片
- 单品拍照
- 商品链接/分享文本
- 商品截图/保存图片
- 未来订单导入

统一流程：
Ingestion → Observation/Evidence → Entity Resolution → WardrobeItem。

### 商品链接流程
粘贴链接/分享文本
→ 解析商品
→ 用户确认 Variant
→ 选择状态：OWNED / ORDERED / WISHLIST / CANDIDATE
→ 建立 WardrobeItem 或 Candidate
→ 生成 Display Asset。

## 八、Garment Digital Twin
一件真实衣物由以下数据共同构成：
- 多次 Observation
- Merchant Evidence
- 用户确认
- Alias
- Wear History
- Availability
- Style Relation
- Display Asset

## 九、Progressive Confirmation
只询问 AI 不确定、但影响决策的属性。
每次最多 1～3 个问题。

## 十、Digital Closet Visualization System
核心：
- Collection Mode
- Visual Shoe Wall
- Visual Shoe Cabinet
- Visual Wardrobe
- Scene Snapshot + Hotspot Layer
- Reality View / AI View

原则：
- Stable Spatial Memory
- 默认不自动移动物品
- AI 整理由用户主动触发
- Reality View 保持极简
- AI View 展示洞察

## 十一、Display Asset
Recognition Asset 与 Display Asset 分离。
Display Asset 包含：
- 标准画布
- Content Bounds
- Anchor Point
- Display Profile
- Visual Scale Factor
- Asset Quality Tier

Fallback Ladder：
1. Primary Standardized Display Asset
2. Secondary Display Asset
3. Canonical Cutout
4. Best Observation Cutout
5. Mirrored Merchant Image
6. Category + Color Placeholder

采用 Progressive Asset Upgrade，不阻塞入库。

## 十二、Wear Event
区分：
- AI Recommended
- User Selected
- User Confirmed Worn
- Photo Verified
- AI Verified

行为信号权重不同。

## 十三、Availability
P0：AVAILABLE / LAUNDRY / UNAVAILABLE。
Outfit Agent 默认只选 AVAILABLE。

## 十四、Personal Stylist 长期价值
生命周期：
Explore → Learn → Maintain → Refresh。

留存循环：
- Daily：今天穿什么
- Weekly：未来一周
- Monthly：旧衣新搭
- Seasonal：换季
- Event：旅行、面试、婚礼、约会

## 十五、Wardrobe-aware Commerce
Existing Wardrobe First。
只有现有衣橱无法解决时才推荐外部商品。

未来评价：
- Wardrobe Compatibility
- Gap Coverage
- Scenario Utility
- Preference Fit
- Redundancy
- Low Usage Risk

## 十六、核心指标
拉新：Diagnosis Completion、Optimization Rate、Share Rate。
建库：首次录入率、5 件激活率、链接入库成功率、Visual Shoe Wall 解锁率。
核心激活：第一次使用自己的真实衣服完成有效 Outfit。
留存：D1/D7/D30、Weekly Personal Styling Decisions。
数据质量：Resolve Rate、Duplicate Error Rate、Confidence Improvement。
商业：Paid Conversion、Renewal、Recommendation→Try-On→Click→Purchase。

## 十七、P0 不做
完整 3D、微服务、闺蜜帮搭、大型商品池、真实尺码预测、全相册扫描、全平台电商抓取、复杂洗衣 ERP、完整离线衣橱、高级上传断点续传。

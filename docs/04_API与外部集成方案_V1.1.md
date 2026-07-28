# AI Wardrobe API 与外部集成方案 V1.1

## 一、原则
REST First、Async Job First、Idempotency First、OpenAPI Contract。

## 二、API 版本策略
/api/v1/ 代表 Breaking Contract Version。
非破坏性变更（新增可选字段、新增 endpoint、新增 enum）直接在 v1 上迭代。
破坏性变更（删除字段、修改字段类型、修改核心语义、删除 endpoint）启动 v2。
兼容策略：至少支持 2 个客户端发布周期，且原则上不少于 90 天。
客户端 Header：X-Client-Version、X-Platform、X-App-Channel。
后端维护 minimum_supported_version 和 recommended_version，支持严重安全问题时的强制升级。

## 三、身份
POST /api/v1/auth/wechat/login
GET /api/v1/me
PATCH /api/v1/me/profile

## 四、资产
POST /api/v1/assets/upload-ticket
POST /api/v1/assets/{id}/complete
DELETE /api/v1/assets/{id}

## 五、诊断
POST /api/v1/style-diagnoses
GET /api/v1/style-diagnoses/{id}
POST /api/v1/style-diagnoses/{id}/optimizations
GET /api/v1/style-optimizations/{id}

## 六、Job
GET /api/v1/jobs/{jobId}

## 七、Ingestion
POST /api/v1/ingestions
GET /api/v1/ingestions/{id}
POST /api/v1/ingestions/{id}/confirm-variant
POST /api/v1/ingestions/{id}/confirm-ownership

## 八、Wardrobe
GET /api/v1/wardrobe/items
GET /api/v1/wardrobe/items/{id}
PATCH /api/v1/wardrobe/items/{id}
DELETE /api/v1/wardrobe/items/{id}
POST /api/v1/wardrobe/items/{id}/confirm-attributes
POST /api/v1/wardrobe/items/{id}/state

## 九、Outfit
POST /api/v1/outfits/generate
GET /api/v1/outfits/{id}

## 十、Look/Wear
POST /api/v1/looks
POST /api/v1/looks/{id}/select
POST /api/v1/looks/{id}/worn
POST /api/v1/looks/{id}/verify-photo
POST /api/v1/wear-events/{id}/feedback

## 十一、Display Scene
GET /api/v1/display-scenes
POST /api/v1/display-scenes
GET /api/v1/display-scenes/{id}
POST /api/v1/display-scenes/{id}/organize
POST /api/v1/display-scenes/{id}/layouts/{layoutId}/activate
PATCH /api/v1/display-slots/{id}

## 十二、分享
POST /api/v1/shares
GET /api/v1/shares/{sceneCode}?attribution_source=WECHAT_FRIEND|WECHAT_TIMELINE
POST /api/v1/shares/{sceneCode}/invocations
POST /api/v1/shares/{sceneCode}/continue
POST /api/v1/votes
GET /api/v1/votes/{sceneCode}/result

## 十三、Commerce
POST /api/v1/purchase-intelligence
POST /api/v1/looks/{id}/recommendations
POST /api/v1/looks/{id}/try-product
POST /api/v1/commerce-events

## 十四、隐私
POST /api/v1/me/deletion-request
GET /api/v1/me/deletion-status
DELETE /api/v1/me/wardrobe
DELETE /api/v1/me/photos/{id}

## 十五、封闭测试反馈
POST /api/v1/feedback
GET /api/v1/me/feedback?limit={1..50}&cursor={opaqueCursor}

反馈列表使用 `{ items, next_cursor }` 游标信封；游标不透明，客户端不得解析或拼接。

## 十六、外部集成原则
微信：登录、分享、图片；剪贴板仅主动操作或用户明确 Opt-in。
COS：直传、Signed URL、私有资产、图片派生。
AI：统一 AI Gateway。
商品数据：公开元数据、授权 API、合法第三方服务、截图降级；不把反爬对抗作为核心路线。

## 十七、幂等
Diagnosis、Optimization、Try-On、Ingestion、Outfit、Purchase Intelligence 必须支持 Idempotency-Key。

## 十八、Rate Limiting
三层限流：IP Layer、User Layer、Costly Action Layer。
初始配置（P0 Target，可调）：
- 普通 API：120 req/min/user
- AI 创建任务（Diagnosis/Optimization/Outfit/PurchaseIntelligence）：10 req/min/user
- Ingestion：30 req/hour/user

Rate Limit 防攻击，Quota 防成本，两者是独立系统。
实现：Redis Token Bucket，返回 429 Too Many Requests + Retry-After Header。

## 十九、请求体限制
- JSON Body：≤ 1MB
- Share Text：≤ 20KB
- 单图 Upload Ticket：默认 ≤ 20MB
- Batch Upload：限制文件数 + 总容量
- Nginx / CLB 层配置 client_max_body_size
- 图片永远不进入 JSON API，通过 COS Upload Ticket 直传
- 服务端必须校验 MIME Type、Magic Number、Image Dimensions，不能只相信客户端文件扩展名

## 二十、Ownership Guard
所有资源操作端点必须校验 resource.user_id == current_user.id。
采用 Scoped Repository：查询时直接 WHERE id = ? AND user_id = ?，不存在"先查出来再判断"遗漏。
OwnershipGuard 作为第二层保护。
Slot 等间接资源通过关系链校验 Ownership：Slot → Layout → Scene → user_id。
display-slots PATCH 额外校验：目标坐标在 Zone 的 bounds 范围内、Layout Status、Hit Region Constraints。

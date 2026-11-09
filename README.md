# Apple Music → 网易云音乐

把 Apple Music 的播放清单分享给网易云的朋友 · [English](README.en.md)

- Apple Music 用户可以把播放清单同步到自己在网易云音乐的清单，方便把歌分享给你们在用网易云的朋友！
- 默认每天自动同步。
- 因歌手和歌名都是转化成原语言再喂给网易云的，无视 Apple Music 的雷霆罗马音/英语翻译。但是还是会出现有些歌不能匹配上的问题，这时候需要自己去网页版网易云找到歌曲的链接然后在程序网页手动关联。

程序会尽量根据歌曲语言获取对应地区的原文资料；部分歌曲或地区资料不可用时会使用备用地区。支持多个清单、网页管理、同步进度、手动纠错，以及可选的 Discord 通知。

## 开始使用

需要 Docker、Apple Music 账号，以及自己可以编辑的网易云歌单。先在网易云创建目标歌单。

**使用 Coolify：** 按照 [Coolify 部署指南](COOLIFY.md) 从 Git 仓库部署，并完成首次设置。

**本地使用：** 在仓库目录运行：

```bash
docker compose build
docker compose run --rm app apple-netease-sync setup
docker compose up -d
```

1. 按提示设置同步时间和两边的清单，并扫码登录网易云。Apple Music 开发者令牌会自动获取，无需手动填写。
2. 打开 [http://localhost:8008](http://localhost:8008)，在设置中完成 Apple Music 授权。
3. 在 **Playlists** 页面管理清单配对，点击 **Run now** 开始同步。

默认每天北京时间 **03:00** 自动同步，可以在网页设置中修改时间和时区。需要保留目标歌单已有歌曲、只追加新歌时，将首次同步和后续移除策略都设为 **preserve**。

## 匹配不对怎么办？

打开 **Needs attention**，点击 **Review**，粘贴正确的网易云歌曲链接，再点击 **Apply correction**。如果已经同步的歌曲有误，先在歌曲详情中点击 **Flag as incorrect**，再手动关联。后续同步会保留手动选择。

同步在后台运行，可以查看进度；运行中的任务也可以使用 **Stop & revert changes** 停止并撤销本次修改。

## 数据与更多说明

保留 Docker 的 `app-data` 数据卷，里面保存账号授权、设置和歌曲关联。网页没有内置登录，请仅在可信网络使用，或添加外部访问认证。

- [匹配算法、同步策略与技术说明](DETAILS.md)
- [Coolify 部署与首次设置](COOLIFY.md)

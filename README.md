# scut_blind_monitor
华南理工大学论文盲审状态监控脚本


## 使用方法
### 方式一：本地脚本
0. 下载仓库代码

1. 安装环境

```bash
pip install -r requirements.txt

playwright install chromium
```

2. Server酱通知
> https://sct.ftqq.com/

复制`SendKey`，填入到`monitor.py`中的`SERVERCHAN_SENDKEY`，保存后退出编辑。

<p align="center">
  <img src="https://github.com/user-attachments/assets/38357f49-c81f-428d-bfaf-3d72310cb81d" alt="Server酱 SendKey 位置示意图" width="600"/>
</p>

3. 运行脚本
```bash
python monitor.py
```
首次使用会弹出浏览器，需要扫码登陆。

<p align="center">
  <img src="https://github.com/user-attachments/assets/8dc1f9b7-7d4c-4318-ac57-a44af57584e8" alt="运行示例" width="500"/>
</p>

### 方式二：OpenClaw
```bash
用clawhub安装这个skill：https://clawhub.ai/songxf1024/scut-review-monitor
```

```bash
查一下我的盲审状态
```

<p align="center">
  <img src="https://github.com/user-attachments/assets/50417931-c78d-471a-ad91-4b24ff3f84d1" alt="OpenClaw 查询盲审状态示例" width="600"/>
</p>



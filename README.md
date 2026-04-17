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

<img width="2062" height="588" alt="image" src="https://github.com/user-attachments/assets/38357f49-c81f-428d-bfaf-3d72310cb81d" />

4. 运行脚本
```bash
python monitor.py
```
首次使用会弹出浏览器，需要扫码登陆。

<img width="880" height="234" alt="image" src="https://github.com/user-attachments/assets/8dc1f9b7-7d4c-4318-ac57-a44af57584e8" />


### 方式二：OpenClaw
https://clawhub.ai/songxf1024/scut-review-monitor


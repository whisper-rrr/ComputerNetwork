Task1 TCP Socket Programming —— README

一、项目简介
本项目基于TCP套接字与自定义应用层协议，实现“文件分块->服务端反转->客户端拼接还原”的完整流程，支持多客户端并发，并通过Wireshark抓包可验证 4 种自定义报文交互过程。
项目包含两个程序：
  - reversetcpserver.py   服务端，负责接收数据并反转后回传
  - reversetcpclient.py   客户端，负责读取文件、分块、发送、接收反转结果并保存
自定义协议共 4 种报文（均采用网络字节序 / 大端序）：
   Type=1  Initialization        [2B Type][4B N]
   Type=2  agree                  [2B Type]
   Type=3  reverseRequest   [2B Type][4B Length][Length B Data]
   Type=4  reverseAnswer     [2B Type][4B Length][Length B Data]

二、运行环境
1. 操作系统
   - Windows 10 / 11
   - Linux (Ubuntu 20.04+ 等)
   （上述系统均已测试可用）
2. Python 版本
   - Python 3.7 及以上（推荐 3.9+）
3. 依赖库
   仅使用 Python 标准库，无需任何第三方依赖
4. 可选工具
   - Wireshark：用于抓取并分析报文，验证协议交互过程
   - Git：用于克隆/管理项目源码

三、文件清单
  reversetcpserver.py         服务端源代码
  reversetcpclient.py          客户端源代码
  input.txt                          待反转的输入文件（由用户自备）
  reversed_output.txt         运行后生成，整体反转结果
  run_log.txt                       运行后生成，带时间戳的客户端运行日志
  run_log_server.txt            运行后生成，带时间戳的服务器端运行日志
  tcp_packet_capture.docx  说明文档（含 Wireshark 抓包分析）
  readme.txt                      本说明文件

四、运行方法
建议先启动服务端，再启动客户端。
【1】启动服务器
    命令格式：
        python reversetcpserver.py <port>
    参数说明：
        port   服务器监听端口，整数，范围 1024 ~ 65535
    示例：
        python reversetcpserver.py 9000
    启动后服务器会输出：
        服务端已启动！监听端口: 9000
        正在等待客户端连接......
【2】启动客户端
    命令格式：
        python reversetcpclient.py <server_ip> <server_port> <file_path> <Lmin> <Lmax> [seed]
    参数说明：
        server_ip      服务器 IP 地址（本机测试可填 127.0.0.1）
        server_port   服务器监听端口（与服务端保持一致）
        file_path       待反转的输入文件路径
        Lmin             每个数据块的最小长度（字节），>0
        Lmax            每个数据块的最大长度（字节），>= Lmin
        seed             可选，随机种子，用于复现同一组分块结果
    示例 1（不指定 seed）：
        python reversetcpclient.py 127.0.0.1 9000 input.txt 10 30
    示例 2（指定 seed，使分块可复现）：
        python reversetcpclient.py 127.0.0.1 9000 input.txt 10 30 42
    运行结束后：
        - 终端会逐块打印反转结果
        - 当前目录生成 reversed_output.txt（完整反转文件）
        - 当前目录生成 run_log.txt（详细运行日志）
        - 程序自动比较 reversed_output.txt 与原文件整体反转结果，
          一致则提示「校验通过」

五、Git 仓库地址
   https://github.com/whisper-rrr/ComputerNetwork.git
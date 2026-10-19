---
name: 🔄 转码表（生成我的专属方案）
description: 上传你的码表文件，自动生成可安装的魔虎派生方案包
title: "转码表：<你的方案名>"
labels: ["转码表"]
body:
  - type: markdown
    attributes:
      value: |
        不用装任何软件、不用命令行：填好下面的表单提交，几分钟后机器人会在这个
        Issue 里回复**方案包下载链接**。

        **适合的码表**：别的「双拼＋辅助码」方案的单字表（txt/tsv），每行
        「编码<Tab>字」或「字<Tab>编码」，其中 4 键行 = 前两键双拼 + 后两键辅助码，
        例如 `aayk→阿`、`bkdp→暴`。两键行（音节）和三键行（渐进）会自动跳过，
        假名/符号行自动忽略，双拼布局自动识别。

  - type: textarea
    id: table-file
    attributes:
      label: 码表文件
      description: >
        把码表 txt/tsv 文件**直接拖进这个框**，会自动变成附件链接。
        小表也可以整段粘贴进来（每行需含 Tab 分隔）。
      placeholder: |
        （把文件拖到这里，或粘贴码表内容）
    validations:
      required: true

  - type: input
    id: scheme-name
    attributes:
      label: 方案名
      description: 小写英文字母开头、仅小写字母与数字（如 mycode）；是装进 Rime 后的方案 ID
      placeholder: mycode
    validations:
      required: true

  - type: input
    id: display-name
    attributes:
      label: 显示名
      description: 输入法选单里显示的名字，可以用中文（如 我的码）；留空则同方案名
      placeholder: 我的码
    validations:
      required: false

  - type: dropdown
    id: layout
    attributes:
      label: 双拼布局
      description: 一般选「自动识别」；识别不对时（构建报错会说明）再来编辑这个 Issue 换成对应布局
      options:
        - 自动识别
        - zrm（自然码）
        - flypy（小鹤）
        - jiajia（加加）
      default: 0
    validations:
      required: true

  - type: dropdown
    id: unique-shortcode
    attributes:
      label: 简码无重
      description: 开启后一简/二简/三简每个码位只留一个字，撞位的字改用完整编码打（喜欢「短键必唯一」手感的人开；不确定就关闭）
      options:
        - "关闭"
        - "开启"
      default: 0
    validations:
      required: true

  - type: markdown
    attributes:
      value: |
        > 💡 提交后请稍等，机器人回复前不要关闭这个 Issue。
        > 如果构建失败，按回复里的提示**编辑这个 Issue** 改好后会自动重新构建。
        > 下载链接 7 天后过期；过期后再开一个新 Issue 重跑即可。

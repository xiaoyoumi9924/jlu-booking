# Contributing

感谢你帮助改进 JLU Booking。提交代码前，请确保改动不会记录或输出 Token、学工号、姓名等个人信息。

## 本地开发

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest
```

请保持真实预约默认关闭。涉及学校接口的改动应尽量补充不访问真实服务的单元测试，并在 pull request 中说明测试平台和验证方式。

## Issue 和日志

提交 Issue 前请搜索是否已有相同问题。粘贴错误信息或日志时，务必删除 Token、学工号、姓名、请求参数和预约记录。

# 贡献指南

感谢对 browscreen 的改进建议。项目介绍与安装步骤见 [README](README.md)，完整资料见[文档导航](doc/README.md)。

## 报告问题

通过 [GitHub Issues](https://github.com/Pegasus-Yang/Browscreen/issues) 提交问题或功能建议。缺陷报告请提供：

- `browscreen version` 输出，以及 Python、操作系统和 Chrome 版本。
- 最小复现步骤、启动参数、预期行为和实际结果。
- 必要的 `-v` 日志片段；提交前移除凭据、私有地址、本机路径和业务画面。

涉及敏感信息的问题可联系维护者 `panesas2@gmail.com`，不要在公开 Issue 中附上敏感值。

## 开发与验证

使用 Python 3.14、`uv` 和项目根目录的 `.venv/`。完整测试还需要 Node.js 22 或更高版本，无需 npm 依赖。

```sh
uv sync --locked --group dev \
  -i http://mirrors.aliyun.com/pypi/simple/ \
  --trusted-host mirrors.aliyun.com
.venv/bin/python -m pytest -q -W error
```

代码位于 `src/browscreen/`，使用完整包导入路径。代码注释和文档字符串使用简体中文，文档字符串采用 reStructuredText 格式；保持现有中英文使用文档同步。

## 提交改动

每个 Pull Request 围绕一个明确的问题，只修改必要文件。说明问题、最终行为及验证结果；修复缺陷时补充能够复现问题的回归测试。功能、参数或使用方式变化时，同步更新对应文档。

默认测试不启动真实 Chrome；报告验证结果时区分自动回归与真实浏览器核验。本机调试、日志、审核和验收证据只在本地保留。

项目采用 [MIT 许可证](LICENSE)。

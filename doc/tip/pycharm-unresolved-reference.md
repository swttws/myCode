# IntelliJ IDEA Unresolved Reference 修复记录

## 问题
IntelliJ IDEA 2026.1 中 `runtime.py` 第 13-21 行的所有 `mycode.*` 导入均显示 "Unresolved reference"。

## 根因
IntelliJ IDEA 项目被识别为纯 Java 项目，缺少 Python 支持的关键配置：

1. `.idea/codeAgent.iml` 缺少 `PythonFacet` 组件（IntelliJ IDEA 通过 facet 添加 Python 支持）
2. `.idea/codeAgent.iml` 中 `orderEntry` 指向 `inheritedJdk`（Java JDK），而非 Python SDK
3. `.idea/misc.xml` 有 `languageLevel="JDK_1_8"`（Java 语言级别）
4. `.idea/misc.xml` 缺少 `PyProjectSettings` 组件
5. `.idea/pySourceRootDetection.xml` 中缺少 `src/` 路径

## 修复内容

### codeAgent.iml
- 模块类型：保持 `JAVA_MODULE`（IntelliJ IDEA 标准）
- 添加 Python SDK 的 `orderEntry`
- 添加 `PythonFacet` 组件

### misc.xml
- 移除 `languageLevel="JDK_1_8"` 和 `default="true"`
- 添加 `PyProjectSettings` 组件

### pySourceRootDetection.xml
- 添加 `$PROJECT_DIR$/src` 到源路径

### 新建文件
- `pyrightconfig.json` — VS Code / Pylance 备用配置
- `.python-version` — 指定 Python 3.11

## 验证
- 用正确的 Python 环境（`C:/miniforge3/envs/codeAgent/python.exe`）导入全部正常
- 所有 13 个导入符号在 `runtime.py` 中均有实际使用
- IntelliJ IDEA 日志显示 Python 插件已加载，项目已索引 4684 个文件

## 后续操作
重启 IntelliJ IDEA 后如果仍有问题，请执行：
1. **File → Invalidate Caches → Invalidate and Restart**（清除索引缓存并重建）
2. 如果仍未解决：**File → Project Structure → Modules → codeAgent → 确认 Python facet 已存在**
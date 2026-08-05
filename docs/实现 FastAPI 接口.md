# Prompt for Cursor: 实现 FastAPI 接口 `/api/v1/remediate`【已完成】

## 1. 任务背景
我们已经有了一个完整的 AI 运维自愈核心库（位于 `remediator/`）。现在需要构建一个 **FastAPI** 服务，对外提供 HTTP 接口，使得 **VS Code / JetBrains 插件** 或其他 **CI/CD 系统** 可以调用我们的自愈能力。

## 2. 核心目标
实现 `POST /api/v1/remediate` 接口，该接口应：
1.  接收终端命令和错误信息。
2.  调用 `remediator.core.executor_wrapper.run_with_remediation`。
3.  返回修复结果、风险等级和诊断信息。

## 3. 项目结构调整
请在项目中创建以下新文件（**不要修改 `remediation/` 下的任何文件**）：

```
remediator/
├── api/
│   ├── __init__.py
│   ├── main.py          # FastAPI App 实例与启动配置
│   ├── endpoints.py     # API 路由定义
│   ├── schemas.py       # Pydantic 请求/响应模型
│   └── deps.py         # 依赖注入（如：获取当前用户、环境校验）
├── core/
│   └── executor_wrapper.py  # 已有文件，确保可被导入
└── pyproject.toml          # 添加 fastapi, uvicorn, python-dotenv 依赖
```

## 4. 详细实现要求

### 4.1 依赖管理 (`pyproject.toml`)
请确保添加了以下依赖：
```toml
dependencies = [
    # ... 已有依赖
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "python-dotenv>=1.0.0",
]
```

### 4.2 API 模型定义 (`api/schemas.py`)
请定义以下 Pydantic 模型：

```python
from pydantic import BaseModel, Field
from typing import Optional, Literal

class RemediateRequest(BaseModel):
    command: str = Field(..., description="用户执行的原始命令")
    stderr: str = Field("", description="命令执行后的标准错误输出")
    stdout: str = Field("", description="命令执行后的标准输出")
    return_code: int = Field(..., description="命令返回码")
    environment_id: str = Field("default", description="环境标识（用于多租户隔离）")
    cwd: str = Field(".", description="当前工作目录")
    confirm_high_risk: bool = Field(False, description="是否自动确认高风险操作")

class RemediateResponse(BaseModel):
    status: Literal["success", "failed", "blocked", "needs_confirmation"]
    original_command: str
    fixed_command: Optional[str] = None
    root_cause: Optional[str] = None
    risk_level: Optional[str] = None
    confidence_score: Optional[float] = None
    message: Optional[str] = None
    metrics: Optional[dict] = None
```

### 4.3 依赖注入 (`api/deps.py`)
```python
from fastapi import Depends, HTTPException, Header
from typing import Annotated

# 模拟一个简单的 API Key 鉴权
async def verify_api_key(x_api_key: Annotated[str | None, Header()] = None):
    # 在实际生产中，这里应该查数据库或配置
    expected_key = "YOUR_SECRET_API_KEY" 
    if x_api_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key

# 获取环境 ID（可以从 Header 或 Body 获取）
def get_env_id(environment_id: str):
    # 这里可以添加环境白名单校验
    return environment_id
```

### 4.4 API 端点实现 (`api/endpoints.py`)
这是核心逻辑，请严格按照以下伪代码实现：

```python
from fastapi import APIRouter, Depends, HTTPException
from remediator.core.executor_wrapper import run_with_remediation, analyze_only
from .schemas import RemediateRequest, RemediateResponse
from .deps import verify_api_key

router = APIRouter(prefix="/api/v1", tags=["Remediation"])

@router.post("/remediate", response_model=RemediateResponse)
async def remediate_command(
    request: RemediateRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    接收命令执行错误，返回 AI 修复建议或执行结果。
    """
    try:
        # 注意：这里我们是同步调用，生产环境建议使用 BackgroundTasks 或 Celery
        # 为了演示，我们直接调用 run_with_remediation
        
        # 构造一个模拟的 CommandExecutionOutcome 或直接调用 wrapper
        # 假设 run_with_remediation 接受这些参数并返回结果
        
        result = run_with_remediation(
            command=request.command,
            stderr=request.stderr,
            stdout=request.stdout,
            return_code=request.return_code,
            environment_id=request.environment_id,
            cwd=request.cwd,
            confirm_high_risk=request.confirm_high_risk,
            dry_run=False  # API 调用默认为执行模式
        )
        
        # 根据 result 的状态映射为 Response
        # 假设 result 是 CommandExecutionOutcome 或类似对象
        return RemediateResponse(
            status="success" if result.return_code == 0 else "failed",
            original_command=request.command,
            fixed_command=getattr(result, 'fixed_command', None),
            root_cause=getattr(result, 'root_cause', None),
            risk_level=getattr(result, 'risk_level', None),
            confidence_score=getattr(result, 'confidence_score', 0.0),
            metrics=getattr(result, 'metrics', {})
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/analyze", response_model=RemediateResponse)
async def analyze_command(
    request: RemediateRequest,
    api_key: str = Depends(verify_api_key)
):
    """仅分析错误，不执行修复（Dry-run 模式）"""
    # 调用 analyze_only 函数
    analysis = analyze_only(...)
    return RemediateResponse(status="needs_confirmation", ...)
```

### 4.5 FastAPI 主应用 (`api/main.py`)
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .endpoints import router as api_router

def create_app():
    app = FastAPI(
        title="AI Remediation API",
        version="1.0.0",
        description="AI-powered command auto-remediation service"
    )
    
    # CORS 配置（允许前端插件调用）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 生产环境请限制域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.include_router(api_router)
    return app

app = create_app()
```

### 4.6 启动脚本 (`api/start_server.py`)
```python
import uvicorn
from .main import create_app

if __name__ == "__main__":
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

## 5. 验收标准
1.  运行 `python -m remediator.api.start_server` 能成功启动服务。
2.  访问 `http://localhost:8000/docs` 能看到 Swagger 文档。
3.  发送 POST 请求到 `/api/v1/remediate`，Body 包含：
    ```json
    {
      "command": "cp /root/file /tmp",
      "stderr": "cp: cannot create regular file '/tmp/file': Permission denied",
      "return_code": 1,
      "environment_id": "local_dev"
    }
    ```
4.  接口能返回修复后的命令（如 `sudo cp ...`）及风险信息。
5.  如果 API Key 错误，返回 403。

## 6. 注意事项
*   请确保在 `remediator/__init__.py` 中正确导出了 `run_with_remediation`。
*   如果 `run_with_remediation` 函数签名与伪代码不符，请根据实际情况调整 `endpoints.py` 中的调用方式。
*   建议在生产环境中使用 Gunicorn + Uvicorn Worker。

请开始编写代码。
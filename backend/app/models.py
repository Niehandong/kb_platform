"""汇总导入所有数据表模型，供数据库迁移识别。新增模型文件后在这里加一行导入。"""

from app.audit import models as audit_models  # noqa: F401
from app.auth import models as auth_models  # noqa: F401
from app.kb import models as kb_models  # noqa: F401

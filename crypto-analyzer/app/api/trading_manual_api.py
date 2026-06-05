"""
交易操作手册 API

端点:
- GET    /api/trading-manual/chapters         获取所有章节
- PUT    /api/trading-manual/chapters/{id}    更新章节内容
- POST   /api/trading-manual/ask              向 AI 提问
- GET    /api/trading-manual/qa               获取问答记录
- POST   /api/trading-manual/qa               保存问答
- PUT    /api/trading-manual/qa/{id}          编辑问答
- DELETE /api/trading-manual/qa/{id}          删除问答
"""
from __future__ import annotations

import json
import os
from typing import Optional
from datetime import datetime

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from app.utils.config_loader import get_db_config


router = APIRouter(prefix="/api/trading-manual", tags=["Trading Manual"])


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


# ============================================================
# DDL — init tables
# ============================================================
_INIT_SQL = """
CREATE TABLE IF NOT EXISTS trading_manual_chapters (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    chapter_number  INT NOT NULL COMMENT '章节号',
    title           VARCHAR(100) NOT NULL COMMENT '章节标题',
    subtitle        VARCHAR(200) DEFAULT '' COMMENT '副标题',
    content         MEDIUMTEXT COMMENT '章节内容(HTML)',
    icon            VARCHAR(50) DEFAULT 'menu_book' COMMENT 'Material图标名',
    color           VARCHAR(20) DEFAULT 'primary' COMMENT '主题色: primary/error/warning/tertiary/info',
    sort_order      INT DEFAULT 0 COMMENT '排序',
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_chapter (chapter_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS trading_manual_qa (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    question        TEXT NOT NULL COMMENT '用户问题',
    answer          TEXT COMMENT '编辑后的回答',
    raw_answer      TEXT COMMENT 'AI原始回答(JSON)',
    source          VARCHAR(20) DEFAULT 'gemini' COMMENT 'AI来源: gemini/deepseek',
    status          VARCHAR(20) DEFAULT 'draft' COMMENT 'draft/published',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# ============================================================
# 初始数据 (9 个章节)
# ============================================================
_INIT_CHAPTERS = [
    (1, '交易铁律', '永远不能打破的规则', 'gavel', 'danger'),
    (2, '识别顶部与底部', '顶底形态 + 量价关系 + 指标确认', 'south', 'warning'),
    (3, '买卖点策略', '分批建仓 + 金字塔加仓 + 止盈方法', 'trending_up', 'info'),
    (4, '风险控制体系', '资金管理 + 仓位管理 + 心理建设', 'shield', 'danger'),
    (5, '合约交易完全指南', '杠杆理解 + 保证金概念 + 合约策略', 'speed', 'tertiary'),
    (6, '交易心理学', '战胜人性弱点', 'psychology', 'tertiary'),
    (7, '交易日志模板', '每次交易后都要填写', 'edit_note', 'info'),
    (8, '常见误区速查', '对照检查，你中了几条？', 'do_not_disturb_alt', 'warning'),
    (9, '系统功能导航', '本系统各页面用途速查', 'explore', 'primary'),
]


# ============================================================
# Models
# ============================================================
class ChapterUpdate(BaseModel):
    content: str


class AskRequest(BaseModel):
    question: str
    source: str = 'gemini'  # gemini / deepseek


class QACreate(BaseModel):
    question: str
    answer: str
    raw_answer: Optional[str] = None
    source: str = 'gemini'


class QAUpdate(BaseModel):
    answer: Optional[str] = None
    status: Optional[str] = None


# ============================================================
# 初始化(启动时调用)
# ============================================================
def init_tables():
    """建表 + 插入初始数据（幂等）"""
    try:
        conn = _connect()
        cur = conn.cursor()
        for stmt in _INIT_SQL.split(';'):
            s = stmt.strip()
            if s:
                cur.execute(s + ';')

        # 插入初始章节（已存在则跳过）
        for ch in _INIT_CHAPTERS:
            cur.execute(
                "INSERT IGNORE INTO trading_manual_chapters "
                "(chapter_number, title, subtitle, icon, color, sort_order) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (ch[0], ch[1], ch[2], ch[3], ch[4], ch[0]),
            )
        cur.close()
        conn.close()
        logger.info("[手册] 数据库表初始化完成")
    except Exception as e:
        logger.error(f"[手册] 初始化失败: {e}")


# ============================================================
# API: 获取所有章节
# ============================================================
@router.get("/chapters")
async def get_chapters():
    """获取所有章节内容"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, chapter_number, title, subtitle, content, icon, color, "
            "DATE_FORMAT(updated_at,'%%Y-%%m-%%d %%H:%%i') AS updated_at "
            "FROM trading_manual_chapters ORDER BY sort_order"
        )
        rows = cur.fetchall()
        return {"success": True, "data": rows}
    finally:
        cur.close()
        conn.close()


# ============================================================
# API: 更新章节内容
# ============================================================
@router.put("/chapters/{chapter_id}")
async def update_chapter(chapter_id: int, body: ChapterUpdate):
    """更新指定章节内容"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE trading_manual_chapters SET content=%s WHERE id=%s",
            (body.content, chapter_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="章节不存在")
        return {"success": True, "message": "保存成功"}
    finally:
        cur.close()
        conn.close()


# ============================================================
# API: AI 问答
# ============================================================
@router.post("/ask")
async def ask_ai(body: AskRequest):
    """向 AI 提问交易相关问题"""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 构建 prompt
    prompt = f"""你是一位资深加密货币交易教练。请用中文回答用户的交易问题。

用户问题：{question}

请给出专业、具体、可执行的建议。回答应包括：
1. 核心观点（一句话总结）
2. 详细分析（如有必要）
3. 具体建议或步骤
4. 风险提示（如果有）

请用 JSON 格式输出，包含字段：summary（一句话总结）、analysis（详细分析）、advice（具体建议）、risk_note（风险提示）。"""

    answer_text = ""
    source_used = body.source

    try:
        if body.source == 'gemini':
            # 调用 Gemini
            from google import genai
            from google.genai import types

            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                # 从 .env 读取
                from dotenv import dotenv_values
                env = dotenv_values()
                api_key = env.get("GEMINI_API_KEY", "")

            model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

            client = genai.Client(api_key=api_key)
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                http_options=types.HttpOptions(timeout=120_000),
            )
            resp = client.models.generate_content(model=model, contents=prompt, config=config)
            answer_text = (resp.text or "").strip()

        else:
            # 调用 DeepSeek
            from openai import OpenAI

            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            if not api_key:
                from dotenv import dotenv_values
                env = dotenv_values()
                api_key = env.get("DEEPSEEK_API_KEY", "") or env.get("DeepSeek_API_KEY", "")

            model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
            base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

            client = OpenAI(api_key=api_key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a professional crypto trading coach. Output ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
                timeout=120,
                response_format={"type": "json_object"},
            )
            answer_text = (resp.choices[0].message.content or "").strip()

    except Exception as e:
        logger.error(f"[手册AI] 调用 {body.source} 失败: {e}")
        raise HTTPException(status_code=502, detail=f"AI 调用失败: {str(e)}")

    # 清理 markdown 代码块
    if answer_text.startswith("```"):
        answer_text = answer_text.strip("`").lstrip("json").strip()

    # 尝试解析为 JSON，如果失败则纯文本返回
    try:
        parsed = json.loads(answer_text)
        formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        parsed = None
        formatted = answer_text

    # 自动保存到 Q&A 记录
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO trading_manual_qa (question, answer, raw_answer, source, status) "
            "VALUES (%s, %s, %s, %s, 'draft')",
            (question, formatted, answer_text, source_used),
        )
        qa_id = cur.lastrowid
    finally:
        cur.close()
        conn.close()

    return {
        "success": True,
        "data": {
            "id": qa_id,
            "question": question,
            "answer": formatted,
            "raw": answer_text,
            "parsed": parsed,
            "source": source_used,
        },
    }


# ============================================================
# API: Q&A CRUD
# ============================================================
@router.get("/qa")
async def get_qa(limit: int = 50):
    """获取问答记录"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, question, answer, source, status, "
            "DATE_FORMAT(created_at,'%%Y-%%m-%%d %%H:%%i') AS created_at, "
            "DATE_FORMAT(updated_at,'%%Y-%%m-%%d %%H:%%i') AS updated_at "
            "FROM trading_manual_qa ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        )
        return {"success": True, "data": cur.fetchall()}
    finally:
        cur.close()
        conn.close()


@router.post("/qa")
async def save_qa(body: QACreate):
    """保存问答记录"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO trading_manual_qa (question, answer, raw_answer, source) "
            "VALUES (%s, %s, %s, %s)",
            (body.question, body.answer, body.raw_answer, body.source),
        )
        return {"success": True, "id": cur.lastrowid}
    finally:
        cur.close()
        conn.close()


@router.put("/qa/{qa_id}")
async def update_qa(qa_id: int, body: QAUpdate):
    """编辑问答记录"""
    conn = _connect()
    cur = conn.cursor()
    try:
        updates = []
        params = []
        if body.answer is not None:
            updates.append("answer=%s")
            params.append(body.answer)
        if body.status is not None:
            updates.append("status=%s")
            params.append(body.status)

        if not updates:
            return {"success": True, "message": "无变更"}

        params.append(qa_id)
        cur.execute(
            f"UPDATE trading_manual_qa SET {', '.join(updates)} WHERE id=%s",
            params,
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="问答记录不存在")
        return {"success": True, "message": "更新成功"}
    finally:
        cur.close()
        conn.close()


@router.delete("/qa/{qa_id}")
async def delete_qa(qa_id: int):
    """删除问答记录"""
    conn = _connect()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM trading_manual_qa WHERE id=%s", (qa_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="问答记录不存在")
        return {"success": True, "message": "已删除"}
    finally:
        cur.close()
        conn.close()

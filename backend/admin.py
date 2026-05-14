"""CLI 管理工具 — 知识库和项目管理"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import get_settings
from backend.models.database import init_db, get_session
from backend.rag.knowledge_base import KnowledgeBaseManager
from backend.services import project_service


def cmd_kb_list():
    """列出所有知识库"""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)
    mgr = KnowledgeBaseManager(db)
    kbs = mgr.list_kbs()
    if not kbs:
        print("No knowledge bases found.")
    else:
        print(f"{'Name':<30} {'Type':<15} {'Description':<40}")
        print("-" * 85)
        for kb in kbs:
            print(f"{kb.name:<30} {kb.type.value if kb.type else 'N/A':<15} {(kb.description or '')[:40]:<40}")
    db.close()


def cmd_kb_delete(name: str):
    """删除知识库"""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)
    mgr = KnowledgeBaseManager(db)
    ok = mgr.delete_kb(name)
    if ok:
        print(f"Deleted knowledge base: {name}")
    else:
        print(f"Knowledge base '{name}' not found.")
    db.close()


def cmd_kb_delete_all():
    """删除所有知识库"""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)
    mgr = KnowledgeBaseManager(db)
    kbs = mgr.list_kbs()
    for kb in kbs:
        mgr.delete_kb(kb.name)
        print(f"Deleted: {kb.name}")
    print(f"Done. {len(kbs)} knowledge bases deleted.")
    db.close()


def cmd_project_delete(project_id: str):
    """删除项目"""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)
    ok = project_service.delete_project(db, project_id)
    if ok:
        print(f"Deleted project: {project_id}")
    else:
        print(f"Project '{project_id}' not found.")
    db.close()


def cmd_project_list():
    """列出所有项目"""
    settings = get_settings()
    engine = init_db(settings.database_url)
    db = get_session(engine)
    projects = project_service.list_projects(db)
    if not projects:
        print("No projects found.")
    else:
        for p in projects:
            print(f"  [{p.status.value}] {p.name}  ({p.id[:8]}...)  {p.created_at}")
    db.close()


def print_usage():
    print("""
Usage: python backend/admin.py <command> [args]

Commands:
  kb list                  List all knowledge bases
  kb delete <name>         Delete a knowledge base by name
  kb delete-all            Delete ALL knowledge bases (careful!)
  project list             List all projects
  project delete <id>      Delete a project by ID (first 8 chars OK)

Examples:
  python backend/admin.py kb list
  python backend/admin.py kb delete security_compliance
  python backend/admin.py kb delete-all
  python backend/admin.py project list
  python backend/admin.py project delete abc12345
""")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print_usage()
        sys.exit(1)

    cmd = args[0]

    if cmd == "kb":
        if len(args) < 2:
            print_usage()
        elif args[1] == "list":
            cmd_kb_list()
        elif args[1] == "delete" and len(args) >= 3:
            cmd_kb_delete(args[2])
        elif args[1] == "delete-all":
            confirm = input("Delete ALL knowledge bases? Type 'yes' to confirm: ")
            if confirm.lower() == "yes":
                cmd_kb_delete_all()
            else:
                print("Cancelled.")
        else:
            print_usage()

    elif cmd == "project":
        if len(args) < 2:
            print_usage()
        elif args[1] == "list":
            cmd_project_list()
        elif args[1] == "delete" and len(args) >= 3:
            cmd_project_delete(args[2])
        else:
            print_usage()

    else:
        print_usage()

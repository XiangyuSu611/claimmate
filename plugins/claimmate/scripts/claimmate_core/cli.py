from .base import *
from .documents import *
from .model import *
from .intake import *
from .projects import *
from .policy import *
from .requirements import *
from .finance import *

def command_guide(_: argparse.Namespace) -> None:
    print("""ClaimMate 快速开始
1. 询问姓名和是否接入邮箱后初始化：claimmate.py init <文件夹> --user-name "姓名" --email-choice skip
2. 展示 Scheme：claimmate.py requirements-show <文件夹>
3. 用户明确确认后完成配置：claimmate.py requirements-confirm <文件夹> --confirmed
4. 新建项目：claimmate.py new <文件夹> --case-name "2026-06_北京出差"
5. 新增材料后：claimmate.py check <文件夹>；消息已说明项目时加 --project。
6. 财务发来反馈时直接转给 ClaimMate；原文和来源会留存，明确要求会进入最终核验。
7. 要交给财务时：claimmate.py ready <文件夹> --project "北京出差"
8. 核验通过后会自动生成逐项报销明细表，收款人默认使用者姓名；报销结束后再 archive。

不同项目分别进入“流程中/项目名称”；无法确定归属的材料暂存在“待处理/待归属”。
每类费用需要哪些材料，以工作区根目录的“报销要求.xlsx”为唯一依据。自动整理会备份原件，最近一次整理可用 undo 撤销。""")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClaimMate 出差报销材料整理器")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="初始化多项目出差报销工作区")
    init_parser.add_argument("folder")
    init_parser.add_argument("--dry-run", action="store_true", help="仅预览，不移动或写入")
    init_parser.add_argument("--case-name", help="可选：同时新建第一个报销项目")
    init_parser.add_argument("--user-name", help="使用者姓名；首次初始化必填，作为交付明细表默认收款人")
    init_parser.add_argument(
        "--email-choice",
        choices=("connect", "skip"),
        help="用户明确选择接入邮箱或暂不接入；首次初始化必填",
    )
    init_parser.add_argument(
        "--no-service",
        action="store_true",
        help="不自动安装后台监听器",
    )
    init_parser.set_defaults(handler=command_init)
    profile_parser = subparsers.add_parser("profile-set", help="更新使用者姓名和默认收款人")
    profile_parser.add_argument("folder")
    profile_parser.add_argument("--user-name", required=True)
    profile_parser.set_defaults(handler=command_profile_set)
    setup_email_parser = subparsers.add_parser("setup-email", help="确认首次配置是否接入邮箱")
    setup_email_parser.add_argument("folder")
    setup_email_parser.add_argument("--choice", choices=("connect", "skip"), required=True)
    setup_email_parser.set_defaults(handler=command_setup_email)
    new_parser = subparsers.add_parser("new", help="新建一个出差报销项目")
    new_parser.add_argument("folder")
    new_parser.add_argument("--case-name", required=True)
    new_parser.set_defaults(handler=command_new)
    list_parser = subparsers.add_parser("list", help="查看所有报销项目")
    list_parser.add_argument("folder")
    list_parser.set_defaults(handler=command_list)
    rename_parser = subparsers.add_parser("rename", help="更新报销项目名称或准确日期")
    rename_parser.add_argument("folder")
    rename_parser.add_argument("--project", required=True)
    rename_parser.add_argument("--new-name", required=True)
    rename_parser.set_defaults(handler=command_rename)
    expense_label_parser = subparsers.add_parser(
        "expense-label", help="按用户确认更新一笔费用的简洁名称并重命名文件"
    )
    expense_label_parser.add_argument("folder")
    expense_label_parser.add_argument("expense_id")
    expense_label_parser.add_argument("--project", required=True)
    expense_label_parser.add_argument("--name", required=True)
    expense_label_parser.set_defaults(handler=command_expense_label)
    expense_merge_parser = subparsers.add_parser(
        "expense-merge", help="按用户明确确认将同一项目中的两笔费用合并"
    )
    expense_merge_parser.add_argument("folder")
    expense_merge_parser.add_argument("--project", required=True)
    expense_merge_parser.add_argument("--target", required=True, help="保留的费用编号")
    expense_merge_parser.add_argument("--source", required=True, help="合并进目标的费用编号")
    expense_merge_parser.set_defaults(handler=command_expense_merge)
    assign_parser = subparsers.add_parser("assign", help="将已有材料批量归入指定项目")
    assign_parser.add_argument("folder")
    assign_parser.add_argument("files", nargs="+")
    assign_parser.add_argument("--project", required=True)
    assign_parser.set_defaults(handler=command_assign)
    check_parser = subparsers.add_parser("check", help="识别新增材料并跨项目分流")
    check_parser.add_argument("folder")
    check_parser.add_argument("--dry-run", action="store_true", help="仅预览，不移动或写入")
    check_parser.add_argument("--project", help="本条消息明确提到的项目")
    check_parser.add_argument(
        "--revisit",
        action="store_true",
        help="即使没有新文件，也用大模型重审待归属和待确认材料",
    )
    check_parser.set_defaults(handler=command_check)
    status_parser = subparsers.add_parser("status", help="查看完整性和待处理事项")
    status_parser.add_argument("folder")
    status_parser.add_argument("--project")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=command_status)
    feedback_add_parser = subparsers.add_parser(
        "feedback-add", help="记录财务反馈并提炼为最终核验依据"
    )
    feedback_add_parser.add_argument("folder")
    feedback_add_parser.add_argument("--text", help="财务反馈原文")
    feedback_add_parser.add_argument("--source-file", help="财务反馈截图、PDF 或原始文件")
    feedback_add_parser.add_argument("--source", help="来源说明，例如财务微信或邮件")
    feedback_add_parser.add_argument("--received-at", help="反馈时间；默认当前时间")
    feedback_add_parser.add_argument("--project", help="仅适用于指定报销项目；不填则为全局规则")
    feedback_add_parser.add_argument("--category", help="仅适用于指定费用类型")
    feedback_add_parser.add_argument("--expense-type", help="大模型从反馈中提取的费用类型，例如出租车票")
    feedback_add_parser.add_argument("--merchant", help="仅适用于名称包含该文本的商户")
    feedback_add_parser.add_argument("--amount-over", help="仅适用于金额超过该值的费用")
    feedback_add_parser.add_argument(
        "--require-evidence",
        action="append",
        help="最终核验必须具备的规范材料类型；可重复，使用 | 表示可替代材料",
    )
    feedback_add_parser.add_argument(
        "--finance-requirement",
        action="append",
        help="大模型提取的财务其他要求；可重复",
    )
    feedback_add_parser.add_argument(
        "--apply-to-scheme",
        action="store_true",
        help="将以后都适用的反馈写入全局报销要求.xlsx",
    )
    feedback_add_parser.add_argument(
        "--scheme-mode",
        choices=("append", "replace"),
        default="append",
        help="追加到现有要求，或用确认后的完整内容替换",
    )
    feedback_add_parser.add_argument(
        "--preview",
        action="store_true",
        help="只显示适用范围和 Scheme 修改前后，不写入",
    )
    feedback_add_parser.add_argument(
        "--confirmed",
        action="store_true",
        help="记录用户已经确认所展示的适用范围和修改内容",
    )
    feedback_add_parser.set_defaults(handler=command_feedback_add)
    feedback_list_parser = subparsers.add_parser("feedback-list", help="查看已记录的财务反馈")
    feedback_list_parser.add_argument("folder")
    feedback_list_parser.add_argument("--project", help="查看对指定项目生效的反馈")
    feedback_list_parser.add_argument("--include-inactive", action="store_true")
    feedback_list_parser.set_defaults(handler=command_feedback_list)
    feedback_status_parser = subparsers.add_parser(
        "feedback-status", help="启用或停用未写入全局 Scheme 的财务反馈要求"
    )
    feedback_status_parser.add_argument("folder")
    feedback_status_parser.add_argument("feedback_id")
    feedback_status_parser.add_argument("--status", choices=("active", "inactive"), required=True)
    feedback_status_parser.add_argument("--reason")
    feedback_status_parser.set_defaults(handler=command_feedback_status)

    requirements_parser = subparsers.add_parser(
        "requirements-validate", help="验证报销要求工作簿并更新有效快照"
    )
    requirements_parser.add_argument("folder")
    requirements_parser.set_defaults(handler=command_requirements_validate)
    requirements_show_parser = subparsers.add_parser(
        "requirements-show", help="用三列表格展示当前 Scheme 和相对上次确认的变化"
    )
    requirements_show_parser.add_argument("folder")
    requirements_show_parser.set_defaults(handler=command_requirements_show)
    requirements_change_parser = subparsers.add_parser(
        "requirements-change", help="预览并按用户确认修改三列 Scheme"
    )
    requirements_change_parser.add_argument("folder")
    requirements_change_parser.add_argument("--expense-type", required=True)
    requirements_change_parser.add_argument("--require-evidence", action="append")
    requirements_change_parser.add_argument("--finance-requirement", action="append")
    requirements_change_parser.add_argument(
        "--scheme-mode", choices=("append", "replace"), default="append"
    )
    requirements_change_parser.add_argument("--preview", action="store_true")
    requirements_change_parser.add_argument("--confirmed", action="store_true")
    requirements_change_parser.set_defaults(handler=command_requirements_change)
    requirements_confirm_parser = subparsers.add_parser(
        "requirements-confirm", help="在用户看过当前 Scheme 后完成首次配置"
    )
    requirements_confirm_parser.add_argument("folder")
    requirements_confirm_parser.add_argument("--confirmed", action="store_true")
    requirements_confirm_parser.add_argument(
        "--no-service", action="store_true", help="确认配置但不安装后台监听器"
    )
    requirements_confirm_parser.set_defaults(handler=command_requirements_confirm)
    ready_parser = subparsers.add_parser("ready", help="交给财务前汇总不确定项和缺失材料")
    ready_parser.add_argument("folder")
    ready_parser.add_argument("--project")
    ready_parser.set_defaults(handler=command_ready)
    resolve_parser = subparsers.add_parser("resolve", help="根据文档线索确认一个观察中的文件")
    resolve_parser.add_argument("folder")
    resolve_parser.add_argument("file", help="原文件名、当前路径或 SHA-256 前缀")
    resolve_parser.add_argument("--role", choices=("invoice", "payment", "supporting"))
    resolve_parser.add_argument("--category", help="费用类型键、名称或文件夹名")
    resolve_parser.add_argument("--expense-name", help="用户确认的简洁费用名称，例如机票或住宿费")
    resolve_parser.add_argument("--expense-id")
    resolve_parser.add_argument("--project")
    resolve_parser.set_defaults(handler=command_resolve)
    export_parser = subparsers.add_parser("export", help="导出报销汇总和缺失清单")
    export_parser.add_argument("folder")
    export_parser.add_argument("--project")
    export_parser.set_defaults(handler=command_export)
    undo_parser = subparsers.add_parser("undo", help="撤销最近一次整理")
    undo_parser.add_argument("folder")
    undo_parser.set_defaults(handler=command_undo)
    archive_parser = subparsers.add_parser("archive", help="导出并创建归档 ZIP")
    archive_parser.add_argument("folder")
    archive_parser.add_argument("--project")
    archive_parser.add_argument("--force", action="store_true", help="明确接受未解决项并归档")
    archive_parser.set_defaults(handler=command_archive)
    guide_parser = subparsers.add_parser("guide", help="显示快速使用指引")
    guide_parser.set_defaults(handler=command_guide)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
        return 0
    except KeyboardInterrupt:
        print("操作已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

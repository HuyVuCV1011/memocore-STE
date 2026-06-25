from __future__ import annotations

import re


RELATIONSHIP_LABELS = {
    "mindx_te_nghia": "nhân sự thuộc nhóm TE tại MindX.",
    "mindx_tegl_direct": "quản lý trực tiếp nhánh TEGL của anh tại MindX.",
    "mindx_tegl_plus_peer": "đồng nghiệp cùng lớp TEGL+ tại MindX.",
    "mindx_tegl_plus_direct": "nhân sự trực tiếp trong nhánh TEGL+ tại MindX.",
    "mindx_tegl_plus_direct_and_ste_collaborator": "nhân sự trực tiếp tại MindX và cộng tác viên tin cậy của STE.",
    "mindx_tom_direct_and_ste_collaborator": "nhân sự trực tiếp trong nhánh TOM tại MindX và cộng tác viên thực thi quan trọng của STE.",
    "mindx_tom_layer2_and_ste_support": "nhân sự lớp dưới TOM tại MindX và có hỗ trợ vận hành cho STE.",
    "mindx_success_ss_under_hieu": "thuộc nhóm Success/SS tại MindX, báo cáo cho anh Hiếu.",
    "mindx_direct_manager": "quản lý trực tiếp của anh tại MindX.",
    "mindx_cross_functional_counterpart": "đối tác phối hợp liên phòng ban tại MindX.",
    "mindx_hcm14_under_hoang_anh": "nhân sự MindX thuộc nhánh HCM1/HCM4 của Hoàng Anh.",
    "mindx_hcm23_under_son": "nhân sự MindX thuộc nhánh HCM2/HCM3 của Quang Sơn.",
    "mindx_cl_hoang_anh": "nhân sự Curriculum/CL trong nhánh của Hoàng Anh tại MindX.",
    "mindx_al_quang_son": "nhân sự Academic/AL trong nhánh của Quang Sơn tại MindX.",
    "mindx_rl_quang_son": "nhân sự Regional/RL trong nhánh của Quang Sơn tại MindX.",
    "mindx_tc_hoang_anh": "nhân sự Training/TC trong nhánh của Hoàng Anh tại MindX.",
    "mindx_peer_branch_under_ha_sua": "đồng nghiệp ở nhánh song song của Hà Sữa tại MindX.",
    "ste_collaborator_historical_mindx": "cộng tác viên STE, từng có bối cảnh làm việc tại MindX.",
    "ste_external_project_reference": "người liên quan đến một dự án bên ngoài của STE.",
}


def relationship_label(value: str) -> str:
    if not value:
        return ""
    if "_" not in value:
        return value.rstrip(".") + "."
    return RELATIONSHIP_LABELS.get(
        value,
        "có liên hệ công việc trong dữ liệu; vai trò cụ thể cần xác nhận.",
    )


def person_note_lines(notes: str) -> list[str]:
    if not notes:
        return []
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", notes)
        if part.strip()
    ]
    return [translated for sentence in sentences if (translated := translate_person_note(sentence))]


def translate_person_note(note: str) -> str:
    normalized = note.casefold()
    replacements = {
        "mindx: tegl hcm 2 & hcm 3 under vu's tegl+ role.": "MindX: phụ trách TEGL HCM2 và HCM3 trong nhánh TEGL+ của anh.",
        "mindx: tegl hcm 1 & hcm 4 under vu's tegl+ role.": "MindX: phụ trách TEGL HCM1 và HCM4 trong nhánh TEGL+ của anh.",
        "mindx: leader team ho / teaching development leader under vu's tom role.": "MindX: leader team HO/Teaching Development trong nhánh TOM của anh.",
        "ste: major execution collaborator.": "STE: cộng tác viên thực thi quan trọng.",
        "ste: high-trust technical/product collaborator.": "STE: cộng tác viên kỹ thuật/sản phẩm tin cậy.",
        "keep contexts separate.": "Cần tách rõ bối cảnh MindX và STE.",
    }
    if normalized in replacements:
        return replacements[normalized]
    cleaned = note.replace("Vu's", "của anh").replace("Vu", "anh")
    cleaned = cleaned.replace("under", "thuộc").replace("Current", "Hiện là")
    return cleaned.strip()

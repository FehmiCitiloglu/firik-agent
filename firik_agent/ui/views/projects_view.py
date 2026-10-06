"""firik masaüstü uygulaması - Gelişmiş Projeler Çalışma Alanı (Advanced Projects Workspace).

Mevcut projelerin kaynak kodunu, gereksinimlerini, görevlerini, Kanban tahtasını,
roadmap'ini, teknik kararlarını (ADR), prompt kütüphanesini, dokümantasyonunu,
Git geçmişini, uzun süreli hafızasını ve bağımsız çalışan AI Project Manager ile
kodlama oturumlarını yöneten merkezi masaüstü çalışma alanı.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from firik_agent.projects.analyzer import detect_tech_stack
from firik_agent.projects.git_service import GitService
from firik_agent.projects.indexer import is_sensitive_file
from firik_agent.projects.workspace import workspace_path
from firik_agent.projects.models import (
    FeatureStatus,
    Project,
    Task,
    TaskStatus,
)
from firik_agent.projects.service import ProjectService
from firik_agent.ui.service import FirikService
from firik_agent.ui.editor import open_in_editor
from firik_agent.ui.views.git_view import GitView
from firik_agent.ui.coding_workspace import CodingWorkspaceMixin
from firik_agent.ui.coding_evidence import CodingEvidenceMixin


class ProjectsView(CodingEvidenceMixin, CodingWorkspaceMixin, ctk.CTkFrame):
    """Ana Projeler Çalışma Alanı Görünümü."""

    def __init__(self, master: Any, service: FirikService, **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.service = service
        self.project_service: ProjectService = getattr(service, "projects", None) or ProjectService()

        self._coding_states: Dict[str, Dict[str, Any]] = {}
        self.current_project_id: Optional[str] = None
        self.view_mode: str = "Grid"  # Grid, List, Recent, Favorites, Archived
        self.active_workspace_tab: str = "overview"

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Container frames for screen switching
        self.list_container = ctk.CTkFrame(self, fg_color="transparent")
        self.workspace_container = ctk.CTkFrame(self, fg_color="transparent")

        self.list_container.grid(row=0, column=0, sticky="nsew")
        self.workspace_container.grid(row=0, column=0, sticky="nsew")

        self._build_projects_list_screen()
        self.show_list_screen()

    def show_list_screen(self) -> None:
        self.workspace_container.grid_remove()
        self.list_container.grid()
        self.refresh_projects_list()

    def show_workspace_screen(self, project_id: str) -> None:
        self.current_project_id = project_id
        self.project_service.scheduler.set_active_project(project_id)
        self.list_container.grid_remove()
        self.workspace_container.grid()
        self._build_project_workspace_screen()

    # =========================================================================
    # 1. PROJELER ANA EKRANI & KARTLARI
    # =========================================================================

    def _build_projects_list_screen(self) -> None:
        self.list_container.grid_columnconfigure(0, weight=1)
        self.list_container.grid_rowconfigure(1, weight=1)

        # Üst Başlık & Eylem Çubuğu
        top_bar = ctk.CTkFrame(self.list_container, fg_color=("gray90", "#161f2e"), corner_radius=10)
        top_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 10))
        top_bar.grid_columnconfigure(0, weight=1)

        header_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        header_box.grid(row=0, column=0, sticky="w", padx=16, pady=10)

        title_lbl = ctk.CTkLabel(
            header_box,
            text="📁  Projeler Çalışma Alanı",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#38bdf8",
        )
        title_lbl.pack(anchor="w")

        desc_lbl = ctk.CTkLabel(
            header_box,
            text="Yazılım projelerinizin kodunu, görevlerini, mimarisini ve AI yönetimini tek merkezden yönetin.",
            font=ctk.CTkFont(size=11),
            text_color=("gray30", "#94a3b8"),
        )
        desc_lbl.pack(anchor="w")

        btn_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e", padx=16, pady=10)

        new_proj_btn = ctk.CTkButton(
            btn_box,
            text="+ Yeni Proje",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#2563eb", "#3b82f6"),
            hover_color=("#1d4ed8", "#2563eb"),
            height=34,
            command=self._open_new_project_dialog,
        )
        new_proj_btn.pack(side="right")

        # Filtre ve Arama Çubuğu
        filter_bar = ctk.CTkFrame(self.list_container, fg_color="transparent")
        filter_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        filter_bar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            filter_bar,
            placeholder_text="Projelerde ara... (ad, teknoloji, etiket, yol)",
            height=32,
            font=ctk.CTkFont(size=12),
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda _: self.refresh_projects_list())

        # Görünüm Butonları
        view_box = ctk.CTkFrame(filter_bar, fg_color="transparent")
        view_box.grid(row=0, column=1, sticky="e")

        views = [
            ("Grid", "⊞ Izgara"),
            ("List", "☰ Liste"),
            ("Recent", "🕒 Son"),
            ("Favorites", "★ Favoriler"),
            ("Archived", "📦 Arşiv"),
        ]
        self.view_buttons: Dict[str, ctk.CTkButton] = {}
        for vm, label in views:
            btn = ctk.CTkButton(
                view_box,
                text=label,
                width=72,
                height=30,
                font=ctk.CTkFont(size=11),
                fg_color=("#3b82f6", "#1d4ed8") if vm == self.view_mode else "transparent",
                text_color="#ffffff" if vm == self.view_mode else ("gray30", "#cbd5e1"),
                border_width=1 if vm != self.view_mode else 0,
                command=lambda m=vm: self._set_view_mode(m),
            )
            btn.pack(side="left", padx=2)
            self.view_buttons[vm] = btn

        # Proje Kartları Kaydırılabilir Alanı
        self.cards_scroll = ctk.CTkScrollableFrame(self.list_container, fg_color="transparent")
        self.cards_scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        self.list_container.grid_rowconfigure(2, weight=1)

    def _set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        for vm, btn in self.view_buttons.items():
            if vm == mode:
                btn.configure(fg_color=("#3b82f6", "#1d4ed8"), text_color="#ffffff", border_width=0)
            else:
                btn.configure(fg_color="transparent", text_color=("gray30", "#cbd5e1"), border_width=1)
        self.refresh_projects_list()

    def refresh_projects_list(self) -> None:
        # Clear existing cards
        for widget in self.cards_scroll.winfo_children():
            widget.destroy()

        search_q = self.search_entry.get().strip() if hasattr(self, "search_entry") else ""
        projects = self.project_service.list_projects(
            search_query=search_q,
            view_mode=self.view_mode,
        )

        if not projects:
            empty_box = ctk.CTkFrame(self.cards_scroll, fg_color=("gray95", "#161f2e"), corner_radius=12)
            empty_box.pack(fill="x", pady=40, padx=20)

            lbl = ctk.CTkLabel(
                empty_box,
                text="Henüz kayıtlı bir proje bulunmuyor.",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=("gray40", "#94a3b8"),
            )
            lbl.pack(pady=(24, 6))

            sub_lbl = ctk.CTkLabel(
                empty_box,
                text="Yeni bir yerel klasörü içe aktarmak veya Git deposu klonlamak için '+ Yeni Proje' butonunu kullanın.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "#64748b"),
            )
            sub_lbl.pack(pady=(0, 20))
            return

        if self.view_mode == "List":
            self._render_list_view(projects)
        else:
            self._render_grid_view(projects)

    def _render_grid_view(self, projects: List[Project]) -> None:
        # 2 cards per row
        self.cards_scroll.grid_columnconfigure(0, weight=1)
        self.cards_scroll.grid_columnconfigure(1, weight=1)

        for idx, p in enumerate(projects):
            row = idx // 2
            col = idx % 2
            card = self._create_project_card(self.cards_scroll, p)
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)

    def _render_list_view(self, projects: List[Project]) -> None:
        self.cards_scroll.grid_columnconfigure(0, weight=1)
        self.cards_scroll.grid_columnconfigure(1, weight=0)

        for idx, p in enumerate(projects):
            card = self._create_project_card(self.cards_scroll, p, is_list=True)
            card.grid(row=idx, column=0, sticky="ew", padx=6, pady=4)

    def _create_project_card(self, parent: Any, p: Project, is_list: bool = False) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            fg_color=("gray92", "#161f2e"),
            corner_radius=12,
            border_width=1,
            border_color=("gray85", "#1e293b"),
        )
        card.grid_columnconfigure(0, weight=1)

        # Üst Başlık Satırı
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))

        icon_lbl = ctk.CTkLabel(header, text=p.icon, font=ctk.CTkFont(size=20))
        icon_lbl.pack(side="left", padx=(0, 8))

        name_box = ctk.CTkFrame(header, fg_color="transparent")
        name_box.pack(side="left", fill="x", expand=True)

        name_lbl = ctk.CTkLabel(
            name_box,
            text=p.name,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("#172033", "#f8fafc"),
            anchor="w",
        )
        name_lbl.pack(anchor="w")

        branch_badge = ctk.CTkLabel(
            name_box,
            text=f"🌿 {p.active_branch}",
            font=ctk.CTkFont(size=10),
            text_color="#38bdf8",
            anchor="w",
        )
        branch_badge.pack(anchor="w")

        # Favori butonu
        fav_btn = ctk.CTkButton(
            header,
            text="★" if p.is_favorite else "☆",
            width=28,
            height=28,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            text_color="#f59e0b" if p.is_favorite else "#64748b",
            hover_color=("gray80", "#1e293b"),
            command=lambda proj=p: self._toggle_favorite(proj),
        )
        fav_btn.pack(side="right")

        # Açıklama
        desc_text = p.description or "Açıklama belirtilmemiş."
        desc_lbl = ctk.CTkLabel(
            card,
            text=desc_text[:90] + ("..." if len(desc_text) > 90 else ""),
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
            anchor="w",
            justify="left",
        )
        desc_lbl.pack(fill="x", padx=14, pady=2)

        # Yerel Yol
        path_lbl = ctk.CTkLabel(
            card,
            text=f"📁 {p.local_path}",
            font=ctk.CTkFont(size=10),
            text_color=("gray50", "#64748b"),
            anchor="w",
        )
        path_lbl.pack(fill="x", padx=14, pady=(2, 6))

        # Teknoloji Rozetleri
        tech_items = []
        for vals in p.tech_stack.values():
            tech_items.extend(vals)
        if tech_items:
            badge_frame = ctk.CTkFrame(card, fg_color="transparent")
            badge_frame.pack(fill="x", padx=14, pady=4)
            for t in tech_items[:5]:
                b = ctk.CTkLabel(
                    badge_frame,
                    text=f" {t} ",
                    font=ctk.CTkFont(size=10),
                    fg_color=("gray85", "#0f172a"),
                    text_color="#38bdf8",
                    corner_radius=6,
                )
                b.pack(side="left", padx=(0, 4))

        # KPI Sayıları (Tasks, Features, Bugs)
        tasks = self.project_service.db.list_tasks(p.id)
        open_tasks = sum(1 for t in tasks if t.status != TaskStatus.DONE)
        features = self.project_service.db.list_features(p.id)
        in_prog_feat = sum(1 for f in features if f.status in (FeatureStatus.IN_PROGRESS, FeatureStatus.TESTING))
        bugs = self.project_service.db.list_bugs(p.id)
        open_bugs = sum(1 for b in bugs if b.status != "closed")

        kpi_frame = ctk.CTkFrame(card, fg_color=("gray88", "#0f172a"), corner_radius=8)
        kpi_frame.pack(fill="x", padx=14, pady=8)

        kpi_text = f"📋 Görev: {open_tasks}   ✨ Özellik: {in_prog_feat}   🐛 Hata: {open_bugs}"
        kpi_lbl = ctk.CTkLabel(
            kpi_frame,
            text=kpi_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray20", "#cbd5e1"),
        )
        kpi_lbl.pack(side="left", padx=10, pady=6)

        # Kart Alt Çubuğu & Eylem Butonu
        footer = ctk.CTkFrame(card, fg_color="transparent")
        footer.pack(fill="x", padx=14, pady=(2, 12))

        open_btn = ctk.CTkButton(
            footer,
            text="Çalışma Alanını Aç ➔",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#3b82f6", "#1d4ed8"),
            hover_color=("#2563eb", "#1e40af"),
            height=30,
            command=lambda pid=p.id: self.show_workspace_screen(pid),
        )
        open_btn.pack(side="right")

        return card

    def _toggle_favorite(self, p: Project) -> None:
        p.is_favorite = not p.is_favorite
        self.project_service.db.update_project(p)
        self.refresh_projects_list()

    # =========================================================================
    # 2. YENİ PROJE OLUŞTURMA MODALI
    # =========================================================================

    def _open_new_project_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Yeni Proje Ekle - firik")
        dialog.geometry("640x520")
        dialog.transient(self)
        dialog.grab_set()

        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        # Başlık
        head = ctk.CTkFrame(dialog, fg_color=("gray90", "#161f2e"), corner_radius=0)
        head.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        t_lbl = ctk.CTkLabel(
            head,
            text="✨ Yeni Proje Çalışma Alanı Ekle",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#38bdf8",
        )
        t_lbl.pack(anchor="w", padx=20, pady=(14, 2))
        sub_lbl = ctk.CTkLabel(
            head,
            text="Bilgisayarınızdaki mevcut bir klasörü seçin, Git deposu klonlayın veya şablon ile başlayın.",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "#94a3b8"),
        )
        sub_lbl.pack(anchor="w", padx=20, pady=(0, 14))

        # Sekmeli İçerik
        tabview = ctk.CTkTabview(dialog)
        tabview.grid(row=1, column=0, sticky="nsew", padx=16, pady=12)
        tabview.add("Yerel Klasör")
        tabview.add("Git Klonla")
        tabview.add("Şablon ile Başlat")

        # TAB 1: Yerel Klasör
        tab1 = tabview.tab("Yerel Klasör")
        tab1.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab1, text="Proje Klasörü:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", pady=8)
        local_path_entry = ctk.CTkEntry(tab1, placeholder_text="Klasör yolu...")
        local_path_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=8)

        def browse_folder():
            folder = filedialog.askdirectory(title="Proje Klasörünü Seçin")
            if folder:
                local_path_entry.delete(0, "end")
                local_path_entry.insert(0, folder)
                p_name = Path(folder).name
                if not name_entry.get().strip():
                    name_entry.insert(0, p_name)
                # Live tech preview
                stack = detect_tech_stack(folder)
                preview_text = "Tespit Edilen Yığın: " + (", ".join(f"{k}: {', '.join(v)}" for k, v in stack.items()) if stack else "Bilinmiyor / Özel")
                stack_preview_lbl.configure(text=preview_text)

        browse_btn = ctk.CTkButton(tab1, text="Gözat...", width=80, command=browse_folder)
        browse_btn.grid(row=0, column=2, pady=8)

        ctk.CTkLabel(tab1, text="Proje Adı:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=1, column=0, sticky="w", pady=8)
        name_entry = ctk.CTkEntry(tab1, placeholder_text="Örn: firik-agent")
        name_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=8)

        ctk.CTkLabel(tab1, text="Açıklama:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=2, column=0, sticky="w", pady=8)
        desc_entry = ctk.CTkEntry(tab1, placeholder_text="Proje hakkında kısa bilgi...")
        desc_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=8)

        stack_preview_lbl = ctk.CTkLabel(
            tab1,
            text="Tespit Edilen Yığın: Klasör seçildiğinde otomatik taranacaktır.",
            font=ctk.CTkFont(size=11),
            text_color="#38bdf8",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        stack_preview_lbl.grid(row=3, column=0, columnspan=3, sticky="w", pady=10)

        # TAB 2: Git Klonla
        tab2 = tabview.tab("Git Klonla")
        tab2.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab2, text="Depo URL'si:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", pady=8)
        clone_url_entry = ctk.CTkEntry(tab2, placeholder_text="https://github.com/kullanici/depo.git")
        clone_url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=8)

        ctk.CTkLabel(tab2, text="Hedef Dizin:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=1, column=0, sticky="w", pady=8)
        clone_dest_entry = ctk.CTkEntry(tab2, placeholder_text="~/Development")
        clone_dest_entry.insert(0, str(Path.home() / "Development"))
        clone_dest_entry.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=8)

        def browse_clone_dest():
            f = filedialog.askdirectory(title="Hedef Klasörü Seçin")
            if f:
                clone_dest_entry.delete(0, "end")
                clone_dest_entry.insert(0, f)

        clone_browse_btn = ctk.CTkButton(tab2, text="Gözat...", width=80, command=browse_clone_dest)
        clone_browse_btn.grid(row=1, column=2, pady=8)

        # TAB 3: Şablon ile Başlat
        tab3 = tabview.tab("Şablon ile Başlat")
        tab3.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(tab3, text="Şablon Türü:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w", pady=8)
        tpl_options = [
            "Web Application",
            "Mobile App",
            "Backend API",
            "CLI Tool",
            "Library / Package",
            "AI Application",
            "Custom",
        ]
        tpl_menu = ctk.CTkOptionMenu(tab3, values=tpl_options)
        tpl_menu.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=8)

        # Butonlar
        bottom_bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bottom_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))

        def create_action():
            current_tab = tabview.get()
            try:
                if current_tab == "Yerel Klasör":
                    lp = local_path_entry.get().strip()
                    nm = name_entry.get().strip()
                    ds = desc_entry.get().strip()
                    if not lp:
                        messagebox.showwarning("Uyarı", "Lütfen bir proje klasörü belirtin.")
                        return
                    self.project_service.create_project(name=nm, local_path=lp, description=ds)
                elif current_tab == "Git Klonla":
                    url = clone_url_entry.get().strip()
                    dest = clone_dest_entry.get().strip()
                    if not url:
                        messagebox.showwarning("Uyarı", "Lütfen bir Git deposu URL'si girin.")
                        return
                    self.project_service.clone_and_create_project(name="", repository_url=url, destination_parent=dest)
                else:
                    lp = local_path_entry.get().strip() or str(Path.home() / "Development" / (name_entry.get().strip() or "new-project"))
                    nm = name_entry.get().strip() or "Yeni Proje"
                    self.project_service.create_project(name=nm, local_path=lp, template=tpl_menu.get())

                dialog.destroy()
                self.refresh_projects_list()
            except Exception as e:
                messagebox.showerror("Hata", f"Proje oluşturulurken bir hata oluştu:\n{e}")

        cancel_btn = ctk.CTkButton(bottom_bar, text="İptal", fg_color="transparent", border_width=1, command=dialog.destroy)
        cancel_btn.pack(side="right", padx=(8, 0))

        create_btn = ctk.CTkButton(
            bottom_bar,
            text="Proje Çalışma Alanını Oluştur",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#2563eb", "#3b82f6"),
            command=create_action,
        )
        create_btn.pack(side="right")

    # =========================================================================
    # 3. PROJECT WORKSPACE EKRANI (ÜST ÇUBUK, SUB-TABS, DASHBOARD)
    # =========================================================================

    def _build_project_workspace_screen(self) -> None:
        # Clear workspace container
        for w in self.workspace_container.winfo_children():
            w.destroy()

        self.workspace_container.grid_columnconfigure(0, weight=1)
        self.workspace_container.grid_rowconfigure(2, weight=1)

        p = self.project_service.db.get_project(self.current_project_id)
        if not p:
            self.show_list_screen()
            return

        # Üst Çalışma Alanı Başlık Çubuğu
        header = ctk.CTkFrame(self.workspace_container, fg_color=("gray90", "#161f2e"), corner_radius=10)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 6))
        header.grid_columnconfigure(1, weight=1)

        back_btn = ctk.CTkButton(
            header,
            text="← Projeler",
            width=85,
            height=30,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self.show_list_screen,
        )
        back_btn.grid(row=0, column=0, padx=12, pady=10)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=1, sticky="ew", padx=6, pady=10)

        title_line = ctk.CTkLabel(
            title_box,
            text=f"{p.icon}  {p.name}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("gray15", "#f8fafc"),
        )
        title_line.pack(side="left")

        branch_lbl = ctk.CTkLabel(
            title_box,
            text="  Git durumu yükleniyor…",
            font=ctk.CTkFont(size=11),
            text_color="#38bdf8",
        )
        branch_lbl.pack(side="left")
        def display_git(state):
            if self.current_project_id == p.id and branch_lbl.winfo_exists():
                if state.get("is_repository"):
                    text = f"  🌿 {state.get('branch') or 'HEAD'} · {state['changed_files']} değişiklik"
                    if state.get("upstream_known"):
                        text += f" · ↑{state.get('ahead', 0)} ↓{state.get('behind', 0)}"
                else:
                    text = "  Git deposu yok" if "not a git repository" in state.get("error", "").lower() else "  Git durumu okunamadı"
                branch_lbl.configure(text=text)
        source = self.service.git_state
        listener = source.subscribe(p.local_path, lambda state: self.service._dispatch(display_git, state))
        # CustomTkinter binds to the frame's native canvas; that destruction
        # event owns the subscription just as the visible header does.
        header.bind("<Destroy>", lambda event: source.unsubscribe(listener), add="+")
        self.service._coding_background(lambda: source.refresh(p.local_path), display_git, lambda error: None)

        path_lbl = ctk.CTkLabel(
            title_box,
            text=f"  ·  {p.local_path}",
            font=ctk.CTkFont(size=10),
            text_color=("gray50", "#64748b"),
        )
        path_lbl.pack(anchor="w")
        title_box.bind("<Configure>", lambda e: path_lbl.configure(wraplength=max(80, e.width - 12)))

        actions_box = ctk.CTkFrame(header, fg_color="transparent")
        actions_box.grid(row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))

        # Quick Add Button
        quick_add_btn = ctk.CTkButton(
            actions_box,
            text="+ Ekle",
            width=70,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#10b981", "#059669"),
            hover_color=("#059669", "#047857"),
            command=self._open_quick_add_menu,
        )
        quick_add_btn.pack(side="left", padx=4)

        # Run PM Check Button
        pm_check_btn = ctk.CTkButton(
            actions_box,
            text="🤖 AI PM Kontrolü",
            width=130,
            height=30,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=("#2563eb", "#3b82f6"),
            command=self._run_pm_check_action,
        )
        pm_check_btn.pack(side="left", padx=4)

        # Komut Paleti
        cmd_palette_btn = ctk.CTkButton(
            actions_box,
            text="⌘K Palet",
            width=85,
            height=30,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            command=self._open_command_palette,
        )
        cmd_palette_btn.pack(side="left", padx=4)

        # Sub-Navigation Bar
        sub_nav = ctk.CTkFrame(self.workspace_container, fg_color="transparent")
        sub_nav.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        tabs = [
            ("overview", "📊 Genel Bakış"),
            ("coding", "🤖 Kodlama Ajanı"),
            ("git", "🌿 Git God"),
            ("tasks", "📋 Görevler"),
            ("features", "✨ Özellikler"),
            ("backlog", "📥 Backlog"),
            ("roadmap", "🗺️ Roadmap"),
            ("bugs_debt", "🐛 Hatalar & Borç"),
            ("docs_adr", "📚 Doküman & ADR"),
            ("memory", "🧠 Hafıza"),
            ("search_ask", "🔍 Arama & Sor"),
            ("ai_pm", "🛡️ AI PM & Öneriler"),
            ("settings", "⚙️ Ayarlar"),
        ]

        self.tab_buttons: Dict[str, ctk.CTkButton] = {}
        for tid, label in tabs:
            btn = ctk.CTkButton(
                sub_nav,
                text=label,
                height=30,
                font=ctk.CTkFont(size=11),
                fg_color=("#3b82f6", "#1d4ed8") if tid == self.active_workspace_tab else "transparent",
                text_color="#ffffff" if tid == self.active_workspace_tab else ("gray30", "#cbd5e1"),
                border_width=1 if tid != self.active_workspace_tab else 0,
                corner_radius=6,
                command=lambda t=tid: self._switch_workspace_tab(t),
            )
            btn.configure(width=130)
            self.tab_buttons[tid] = btn

        groups = {
            "Planlama": ["tasks", "features", "backlog", "roadmap", "bugs_debt", "ai_pm"],
            "Bilgi": ["docs_adr", "memory", "search_ask"],
        }
        tab_labels = dict(tabs)
        self.workspace_groups = {}
        def choose_group(value, identifiers):
            selected = next((key for key in identifiers if tab_labels[key] == value), None)
            if selected:
                self._switch_workspace_tab(selected)
        for name, identifiers in groups.items():
            values = [name, *[tab_labels[key] for key in identifiers]]
            combo = ctk.CTkComboBox(sub_nav, values=values, state="readonly", width=155,
                command=lambda value, ids=identifiers: choose_group(value, ids))
            combo.set(tab_labels[self.active_workspace_tab] if self.active_workspace_tab in identifiers else name)
            self.workspace_groups[name] = combo
        visible_nav = [self.tab_buttons[key] for key in ("overview", "coding", "git")]
        visible_nav += list(self.workspace_groups.values()) + [self.tab_buttons["settings"]]
        def layout_tabs(event):
            columns = max(1, event.width // 134)
            for index, button in enumerate(visible_nav):
                button.grid(row=index // columns, column=index % columns, padx=2, pady=2, sticky="ew")
        sub_nav.bind("<Configure>", layout_tabs)
        layout_tabs(type("Size", (), {"width": 700})())

        # Alt Ana Çalışma Alanı Paneli
        self.workspace_body = ctk.CTkFrame(self.workspace_container, fg_color="transparent")
        self.workspace_body.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        self.workspace_body.grid_columnconfigure(0, weight=1)
        self.workspace_body.grid_rowconfigure(0, weight=1)

        self._render_active_workspace_tab()

    def _switch_workspace_tab(self, tab_id: str) -> None:
        self.active_workspace_tab = tab_id
        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                btn.configure(fg_color=("#3b82f6", "#1d4ed8"), text_color="#ffffff", border_width=0)
            else:
                btn.configure(fg_color="transparent", text_color=("gray30", "#cbd5e1"), border_width=1)
        self._render_active_workspace_tab()

    def _render_active_workspace_tab(self) -> None:
        for w in self.workspace_body.winfo_children():
            w.destroy()

        for column in range(3):
            self.workspace_body.grid_columnconfigure(column, weight=0, minsize=0)
        for row in range(8):
            self.workspace_body.grid_rowconfigure(row, weight=0, minsize=0)
        self.workspace_body.grid_columnconfigure(0, weight=1)
        self.workspace_body.grid_rowconfigure(0, weight=1)
        tab = self.active_workspace_tab
        if tab == "overview":
            self._render_tab_overview(self.workspace_body)
        elif tab == "coding":
            self._render_tab_coding(self.workspace_body)
        elif tab == "git":
            self._render_tab_git(self.workspace_body)
        elif tab == "tasks":
            self._render_tab_tasks(self.workspace_body)
        elif tab == "features":
            self._render_tab_features(self.workspace_body)
        elif tab == "backlog":
            self._render_tab_backlog(self.workspace_body)
        elif tab == "roadmap":
            self._render_tab_roadmap(self.workspace_body)
        elif tab == "bugs_debt":
            self._render_tab_bugs_debt(self.workspace_body)
        elif tab == "docs_adr":
            self._render_tab_docs_adr(self.workspace_body)
        elif tab == "memory":
            self._render_tab_memory(self.workspace_body)
        elif tab == "search_ask":
            self._render_tab_search_ask(self.workspace_body)
        elif tab == "ai_pm":
            self._render_tab_ai_pm(self.workspace_body)
        elif tab == "settings":
            self._render_tab_settings(self.workspace_body)

    # =========================================================================
    # TAB: GENEL BAKIŞ (OVERVIEW)
    # =========================================================================

    def _render_tab_overview(self, container: Any) -> None:
        ov = self.project_service.get_project_overview(self.current_project_id)
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # 1. KPI Kartları Satırı
        kpi_row = ctk.CTkFrame(scroll, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, 10))
        for i in range(4):
            kpi_row.grid_columnconfigure(i, weight=1)

        cards_data = [
            ("Aktif Görevler", str(ov["active_tasks_count"]), "#38bdf8"),
            ("Geliştirilen Özellikler", str(ov["features_in_dev_count"]), "#818cf8"),
            ("Açık Hatalar", str(ov["open_bugs_count"]), "#ef4444" if ov["open_bugs_count"] > 0 else "#10b981"),
            ("Teknik Borç", str(ov["tech_debt_count"]), "#f59e0b"),
        ]
        for col_idx, (kpi_title, kpi_val, color) in enumerate(cards_data):
            c = ctk.CTkFrame(kpi_row, fg_color=("gray92", "#161f2e"), corner_radius=10)
            c.grid(row=0, column=col_idx, sticky="ew", padx=4, pady=2)
            lbl = ctk.CTkLabel(c, text=kpi_title, font=ctk.CTkFont(size=11), text_color=("gray40", "#94a3b8"))
            lbl.pack(anchor="w", padx=12, pady=(10, 2))
            val_lbl = ctk.CTkLabel(c, text=kpi_val, font=ctk.CTkFont(size=20, weight="bold"), text_color=color)
            val_lbl.pack(anchor="w", padx=12, pady=(0, 10))

        # 2. AI Project Manager İçgörüleri & Sağlık
        pm_box = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
        pm_box.pack(fill="x", pady=6)

        pm_header = ctk.CTkLabel(
            pm_box,
            text="🤖  AI Project Manager Durum Özeti & İçgörüleri",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8",
        )
        pm_header.pack(anchor="w", padx=14, pady=(12, 4))

        latest_chk = ov.get("latest_check")
        summary_txt = latest_chk.summary if latest_chk else "Henüz bir AI Project Manager kontrolü yapılmadı. 'AI PM Kontrolü' butonunu tıklayarak başlatabilirsiniz."
        pm_summary = ctk.CTkLabel(
            pm_box,
            text=summary_txt,
            font=ctk.CTkFont(size=11),
            text_color=("gray20", "#cbd5e1"),
            justify="left",
            wraplength=850,
        )
        pm_summary.pack(anchor="w", padx=14, pady=(2, 12))

        # 3. Son Değişiklikler ve Commitler
        commits_box = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
        commits_box.pack(fill="x", pady=6)

        c_header = ctk.CTkLabel(
            commits_box,
            text="🌿  Son Git Commitleri",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#f8fafc",
        )
        c_header.pack(anchor="w", padx=14, pady=(12, 6))

        commits = ov.get("recent_commits", [])
        if commits:
            for c in commits[:4]:
                line = ctk.CTkLabel(
                    commits_box,
                    text=f"• {c['short_hash']}  {c['message']}  ({c['author']}, {c['relative_date']})",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "#94a3b8"),
                    anchor="w",
                )
                line.pack(fill="x", padx=18, pady=2)
        else:
            no_c = ctk.CTkLabel(commits_box, text="Commit geçmişi bulunamadı.", font=ctk.CTkFont(size=11), text_color="#64748b")
            no_c.pack(anchor="w", padx=18, pady=(0, 10))

    # =========================================================================
    # TAB: KODLAMA AJANI (CODING AGENT - 3-PANE LAYOUT)
    # =========================================================================


    def _load_coding_models(self) -> None:
        combo = self.coding_model_combo

        def on_models(models: List[str]) -> None:
            if combo.winfo_exists():
                combo.configure(values=list(dict.fromkeys([combo.get(), *models])))
                self._refresh_coding_effort()

        self.service.fetch_models_async(on_success=on_models)

    def _render_tab_git(self, container: Any) -> None:
        project = self.project_service.db.get_project(self.current_project_id)
        self.git_view = GitView(container, self.service, project.local_path,
                                on_ask_agent=self._git_ask_agent, on_open_file=self._open_git_file)
        self.git_view.grid(row=0, column=0, sticky="nsew")

    def _open_git_file(self, relative_path: str) -> None:
        project = self.project_service.db.get_project(self.current_project_id)
        editor = self.service.config.get("coding_editor", {})
        try:
            root = GitService._insights_root(project.local_path)
            target = workspace_path(root, relative_path)
            open_in_editor(editor.get("name", "VS Code"), target, editor.get("path", ""))
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("Editör Açılamadı", str(error))

    def _git_ask_agent(self, prompt: str) -> None:
        self._switch_workspace_tab("coding")
        self.coding_tabs.set("Sohbet")
        self.coding_input.delete(0, "end")
        self.coding_input.insert(0, prompt)
        self.coding_input.focus_set()

    def _on_coding_model_selected(self, model: str) -> None:
        state = self._coding_state()
        settings = self.project_service.db.get_settings(self.current_project_id)
        if state["busy"] or not model.strip():
            self.coding_model_combo.set(settings.coding_model or self.service.default_model)
            return
        settings.coding_model = model.strip()
        self.project_service.db.save_settings(settings)
        state["session"].model = settings.coding_model
        self._refresh_coding_effort()

    def _on_coding_editor_selected(self, editor: str) -> None:
        previous = self.service.config.get("coding_editor", {})
        custom_path = previous.get("path", "")
        if editor == "Özel Editör":
            custom_path = filedialog.askopenfilename(title="Editörün uygulama dosyasını seçin")
            if not custom_path:
                self.coding_editor_combo.set(previous.get("name", "VS Code"))
                return
        try:
            self.service.set_coding_editor(editor, custom_path)
        except Exception as error:
            self.coding_editor_combo.set(previous.get("name", "VS Code"))
            messagebox.showerror("Editör Ayarı", str(error))

    def _open_coding_editor(self, target: Optional[str] = None) -> None:
        project = self.project_service.db.get_project(self.current_project_id)
        if not project:
            return
        editor = self.service.config.get("coding_editor", {})
        try:
            path = workspace_path(project.local_path, target or ".")
            open_in_editor(editor.get("name", "VS Code"), path, editor.get("path", ""))
        except (OSError, ValueError) as error:
            messagebox.showerror("Editör Açılamadı", str(error))

    def _open_selected_coding_file(self) -> None:
        path = self._coding_state()["selected_change"]
        if path:
            self._open_coding_editor(path)

    def _coding_visible(self, project_id: str) -> bool:
        return (self.current_project_id == project_id and self.active_workspace_tab == "coding"
                and hasattr(self, "coding_tabs") and self.coding_tabs.winfo_exists())

    def _refresh_coding_changes(self) -> None:
        state = self._coding_state()
        changes = state["changes"]
        added = sum(change["added"] for change in changes)
        removed = sum(change["removed"] for change in changes)
        summary = (f"{'Bu çalışma' if state['busy'] else 'Son çalışma'}: {len(changes)} dosya · +{added} / −{removed} satır"
                   if state["before"] is not None else "Ajanın dosya değişiklikleri burada görünür.")
        if state["inspection_warning"]:
            summary += "\n" + state["inspection_warning"]
        self.coding_changes_summary.configure(text=summary)
        self.coding_refresh_changes_btn.configure(state="disabled" if state["busy"] else "normal")
        for widget in self.coding_changes_list.winfo_children():
            widget.destroy()
        labels = {"added": "Eklendi", "modified": "Değişti", "deleted": "Silindi"}
        for change in changes:
            ctk.CTkButton(
                self.coding_changes_list,
                text=f"{change.get('origin', 'Gözlenen')} · {labels[change['status']]} · {change['path']}  (+{change['added']} / −{change['removed']})",
                anchor="w", height=28, fg_color="transparent",
                command=lambda path=change["path"]: self._show_coding_change(path),
            ).pack(fill="x", pady=2)
        paths = [change["path"] for change in changes]
        if state["selected_change"] not in paths:
            state["selected_change"] = paths[0] if paths else None
        self._show_coding_change(state["selected_change"])

    def _show_coding_change(self, path: Optional[str]) -> None:
        state = self._coding_state()
        state["selected_change"] = path
        change = next((c for c in state["changes"] if c["path"] == path), None)
        self.coding_diff_box.configure(state="normal")
        self.coding_diff_box.delete("1.0", "end")
        if change:
            for index, line in enumerate(change["diff"].splitlines()):
                tag = "header" if index < 2 or line.startswith("@@") else "added" if line.startswith("+") else "removed" if line.startswith("-") else None
                self.coding_diff_box.insert("end", line + "\n", tag)
        else:
            self.coding_diff_box.insert("end", "Henüz karşılaştırılacak değişiklik yok.")
        self.coding_diff_box.configure(state="disabled")
        self.coding_open_file_btn.configure(state="normal" if change and change["status"] != "deleted" else "disabled")
        if hasattr(self, "coding_undo_btn"):
            self.coding_undo_btn.configure(state="normal" if change and change.get("origin") == "Ajan" and not state["busy"] else "disabled")

    def _apply_coding_snapshot(self, state: Dict[str, Any], snapshot) -> None:
        state["snapshot"] = snapshot
        if state["before"] is not None:
            state["changes"] = snapshot.changes or []
            for change in state["changes"]:
                journal = state.get("agent_paths", {}).get(change["path"])
                content = snapshot.files.get(change["path"])
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None
                change["origin"] = "Ajan" if journal and journal.get("after_hash") == digest else "Gözlenen dış düzenleme"
        if state["before"] is None:
            return
        state["inspection_warning"] = (
            "Bazı dosyalar okunamadı veya tarama sınırına ulaşıldı; karşılaştırma kısmi."
            if snapshot.limited or snapshot.skipped or (state["before"] and (state["before"].limited or state["before"].skipped)) else ""
        )

    def _refresh_coding_changes_async(self) -> None:
        state = self._coding_state()
        if state["busy"]:
            return
        project_id = self.current_project_id
        project = self.project_service.db.get_project(project_id)
        run_id = state["run_id"]

        def on_snapshot(snapshot):
            if state["run_id"] != run_id:
                return
            self._apply_coding_snapshot(state, snapshot)
            if self._coding_visible(project_id):
                self._refresh_coding_changes()
                self._render_coding_files(snapshot)

        def on_error(error):
            if state["run_id"] != run_id:
                return
            state["inspection_warning"] = f"Dosyalar karşılaştırılamadı: {error}"
            if self._coding_visible(project_id):
                self._refresh_coding_changes()

        self.service.inspect_workspace_async(project.local_path, on_snapshot, on_error, before=state["before"])

    def _refresh_coding_files_async(self) -> None:
        project_id = self.current_project_id
        project = self.project_service.db.get_project(project_id)
        files_list = self.coding_files_list

        def on_snapshot(snapshot):
            if self._coding_visible(project_id) and self.coding_files_list is files_list:
                self._render_coding_files(snapshot)

        def on_error(error):
            if self._coding_visible(project_id) and self.coding_files_list is files_list:
                ctk.CTkLabel(files_list, text=f"Dosyalar yüklenemedi: {error}", wraplength=350).pack(anchor="w")

        self.service.inspect_workspace_async(project.local_path, on_snapshot, on_error)

    def _render_coding_files(self, snapshot) -> None:
        for widget in self.coding_files_list.winfo_children():
            widget.destroy()
        if not snapshot.files:
            ctk.CTkLabel(self.coding_files_list, text="Gösterilecek kaynak dosya bulunamadı.").pack(anchor="w")
        if snapshot.limited or snapshot.skipped:
            ctk.CTkLabel(self.coding_files_list, text="Dosya listesi kısmi; büyük ve okunamayan dosyalar atlandı.", wraplength=350).pack(anchor="w")
        for path in list(snapshot.files)[:120]:
            ctk.CTkButton(self.coding_files_list, text=path, anchor="w", height=26, fg_color="transparent",
                          command=lambda p=path: self._preview_coding_file(p)).pack(fill="x", pady=1)

    def _preview_coding_file(self, relative_path: str, line_number: int = 1) -> None:
        project = self.project_service.db.get_project(self.current_project_id)
        try:
            path = workspace_path(project.local_path, relative_path)
            if is_sensitive_file(path, project_root=project.local_path):
                raise ValueError("Bu dosya kaynak önizlemesine dahil edilmiyor. Editörde açabilirsiniz.")
            with path.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024 or b"\x00" in data:
                raise ValueError("Bu dosya önizleme için çok büyük veya metin dosyası değil. Editörde açabilirsiniz.")
            content = data.decode("utf-8")
        except (OSError, ValueError) as error:
            messagebox.showerror("Dosya Önizlemesi", str(error))
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title(relative_path)
        dialog.geometry("850x600")
        dialog.transient(self.winfo_toplevel())
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(dialog, text=relative_path, anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        box = ctk.CTkTextbox(dialog, wrap="none", font=ctk.CTkFont(family="monospace", size=12))
        box.grid(row=1, column=0, sticky="nsew", padx=12)
        numbered = "\n".join(f"{number:4}  {line}" for number, line in enumerate(content.splitlines(), 1))
        box.insert("end", numbered)
        line_number = max(1, min(line_number, max(1, len(content.splitlines()))))
        box.tag_config("reference", background="#304860")
        box.tag_add("reference", f"{line_number}.0", f"{line_number}.end")
        box.see(f"{line_number}.0")
        box.configure(state="disabled")
        editor = dict(self.service.config.get("coding_editor", {}))

        def open_file():
            try:
                open_in_editor(editor.get("name", "VS Code"), path, editor.get("path", ""))
            except (OSError, ValueError) as error:
                messagebox.showerror("Editör Açılamadı", str(error))

        ctk.CTkButton(dialog, text="Editörde Aç", command=open_file).grid(row=2, column=0, sticky="e", padx=12, pady=10)

    def _refresh_coding_activity(self) -> None:
        return CodingEvidenceMixin._refresh_coding_activity(self)

    def _show_coding_activity(self, activity: Dict[str, Any]) -> None:
        return CodingEvidenceMixin._show_coding_activity(self, activity)

    def _handle_coding_return(self, event=None) -> str:
        self.coding_send_btn.invoke()
        return "break"

    def _send_coding_prompt(self, event=None) -> str:
        txt = self.coding_input.get().strip()
        if txt and not self._coding_state()["busy"]:
            self.coding_input.delete(0, "end")
            self._run_coding_prompt(txt)
        return "break"


    # =========================================================================
    # TAB: GÖREVLER & KANBAN (TASKS)
    # =========================================================================

    def _render_tab_tasks(self, container: Any) -> None:
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        t_lbl = ctk.CTkLabel(top, text="📋 Görevler ve Kanban Tahtası", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        t_lbl.pack(side="left")

        add_task_btn = ctk.CTkButton(
            top,
            text="+ Yeni Görev Ekle",
            height=28,
            command=self._open_new_task_dialog,
        )
        add_task_btn.pack(side="right")

        # Kanban Sütunları
        board_data = self.project_service.get_kanban_board_data(self.current_project_id)
        board_scroll = ctk.CTkScrollableFrame(container, fg_color="transparent", orientation="horizontal")
        board_scroll.grid(row=1, column=0, sticky="nsew")

        cols = ["BACKLOG", "TODO", "IN PROGRESS", "REVIEW", "TESTING", "DONE"]
        for idx, col_name in enumerate(cols):
            tasks_in_col = board_data.get(col_name, [])
            col_frame = ctk.CTkFrame(board_scroll, width=220, fg_color=("gray92", "#161f2e"), corner_radius=10)
            col_frame.pack(side="left", fill="y", padx=4, pady=2)
            col_frame.pack_propagate(False)

            # Sütun Başlığı
            head = ctk.CTkLabel(
                col_frame,
                text=f"{col_name} ({len(tasks_in_col)})",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#f8fafc",
            )
            head.pack(anchor="w", padx=10, pady=(8, 6))

            task_sub_scroll = ctk.CTkScrollableFrame(col_frame, fg_color="transparent")
            task_sub_scroll.pack(fill="both", expand=True, padx=4, pady=4)

            for t in tasks_in_col:
                self._render_kanban_task_card(task_sub_scroll, t)

    def _render_kanban_task_card(self, parent: Any, t: Task) -> None:
        c = ctk.CTkFrame(parent, fg_color=("gray85", "#0f172a"), corner_radius=8)
        c.pack(fill="x", pady=3)

        title = ctk.CTkLabel(c, text=t.title, font=ctk.CTkFont(size=11, weight="bold"), wraplength=180, justify="left", anchor="w")
        title.pack(fill="x", padx=8, pady=(6, 2))

        meta = ctk.CTkLabel(c, text=f"{t.priority} · {t.task_type}", font=ctk.CTkFont(size=10), text_color="#38bdf8", anchor="w")
        meta.pack(fill="x", padx=8, pady=1)

        # "Ajan ile Başlat" butonu
        agent_btn = ctk.CTkButton(
            c,
            text="⚡ Ajan ile Başlat",
            height=22,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("#2563eb", "#3b82f6"),
            command=lambda tid=t.id: self._start_task_with_agent(tid),
        )
        agent_btn.pack(fill="x", padx=8, pady=(4, 6))


    # =========================================================================
    # TAB: ÖZELLİKLER (FEATURES)
    # =========================================================================

    def _render_tab_features(self, container: Any) -> None:
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        t_lbl = ctk.CTkLabel(top, text="✨ Özellikler ve Kod Gerçekleştirim Durumu", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        t_lbl.pack(side="left")

        add_feat_btn = ctk.CTkButton(top, text="+ Yeni Özellik Ekle", height=28, command=self._open_new_feature_dialog)
        add_feat_btn.pack(side="right")

        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        features = self.project_service.db.list_features(self.current_project_id)
        if not features:
            ctk.CTkLabel(scroll, text="Henüz bir özellik tanımlanmamış.", font=ctk.CTkFont(size=12), text_color="#94a3b8").pack(pady=20)
            return

        for f in features:
            fc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
            fc.pack(fill="x", pady=4)

            head = ctk.CTkLabel(fc, text=f.title, font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc", anchor="w")
            head.pack(fill="x", padx=12, pady=(10, 2))

            desc = ctk.CTkLabel(fc, text=f.description or "Açıklama yok.", font=ctk.CTkFont(size=11), text_color=("gray40", "#94a3b8"), anchor="w")
            desc.pack(fill="x", padx=12, pady=2)

            # Implementation status matrix (Section 8)
            matrix = ctk.CTkFrame(fc, fg_color=("gray85", "#0f172a"), corner_radius=6)
            matrix.pack(fill="x", padx=12, pady=(6, 10))

            impl = f.implementation_status
            badges = [
                ("Backend API", impl.get("backend_api") == "implemented"),
                ("Database", impl.get("database_schema") == "implemented"),
                ("Frontend UI", impl.get("frontend_ui") == "implemented"),
                ("Unit Tests", impl.get("unit_tests") == "implemented"),
                ("Integration Tests", impl.get("integration_tests") == "implemented"),
                ("Docs", impl.get("documentation") == "implemented"),
            ]
            for title, ok in badges:
                badge_lbl = ctk.CTkLabel(
                    matrix,
                    text=f"{'✅' if ok else '❌'} {title}",
                    font=ctk.CTkFont(size=10),
                    text_color="#10b981" if ok else "#ef4444",
                )
                badge_lbl.pack(side="left", padx=8, pady=4)

    # =========================================================================
    # TAB: BACKLOG
    # =========================================================================

    def _render_tab_backlog(self, container: Any) -> None:
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        t_lbl = ctk.CTkLabel(top, text="📥 Proje Backlog Listesi & AI Analizi", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        t_lbl.pack(side="left")

        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # AI Backlog Analizi Kutusu
        recs = self.project_service.analyze_backlog(self.current_project_id)
        ai_box = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
        ai_box.pack(fill="x", pady=(0, 10))

        head = ctk.CTkLabel(ai_box, text="💡 AI Backlog Önerileri", font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8")
        head.pack(anchor="w", padx=12, pady=(10, 4))

        for r in recs:
            rl = ctk.CTkLabel(ai_box, text=f"• {r}", font=ctk.CTkFont(size=11), text_color=("gray20", "#cbd5e1"), justify="left", wraplength=800)
            rl.pack(anchor="w", padx=16, pady=2)
        ctk.CTkLabel(ai_box, text="", height=4).pack()

        # Backlog Görevleri
        tasks = [t for t in self.project_service.db.list_tasks(self.current_project_id) if t.status == TaskStatus.BACKLOG]
        for t in tasks:
            tc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=8)
            tc.pack(fill="x", pady=3)
            ctk.CTkLabel(tc, text=f"• {t.title} [{t.priority}]", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=12, pady=6)

    # =========================================================================
    # TAB: ROADMAP & MILESTONES
    # =========================================================================

    def _render_tab_roadmap(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        t_lbl = ctk.CTkLabel(scroll, text="🗺️ Zaman Çizelgesi & Kilometre Taşları (Roadmap)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8")
        t_lbl.pack(anchor="w", pady=(0, 10))

        milestones = self.project_service.db.list_milestones(self.current_project_id)
        if not milestones:
            ctk.CTkLabel(scroll, text="Henüz bir kilometre taşı tanımlanmamış.", font=ctk.CTkFont(size=11), text_color="#94a3b8").pack(pady=20)
            return

        for m in milestones:
            mc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
            mc.pack(fill="x", pady=6)

            h = ctk.CTkLabel(mc, text=f"🎯 {m.title} (Hedef: {m.target_date or 'Belirtilmedi'})", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc")
            h.pack(anchor="w", padx=12, pady=(10, 4))

            # Progress Bar
            progress = ctk.CTkProgressBar(mc, height=10)
            progress.pack(fill="x", padx=12, pady=4)
            progress.set(m.completion_pct / 100.0)

            pct_lbl = ctk.CTkLabel(mc, text=f"Tamamlanma: %{m.completion_pct}", font=ctk.CTkFont(size=10), text_color="#38bdf8")
            pct_lbl.pack(anchor="w", padx=12, pady=(0, 10))

    # =========================================================================
    # TAB: HATALAR & TEKNİK BORÇ (BUGS & DEBT)
    # =========================================================================

    def _render_tab_bugs_debt(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="🐛 Hata Takibi (Bugs)", font=ctk.CTkFont(size=13, weight="bold"), text_color="#ef4444").pack(anchor="w", pady=(0, 4))
        bugs = self.project_service.db.list_bugs(self.current_project_id)
        if bugs:
            for b in bugs:
                bc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=8)
                bc.pack(fill="x", pady=3)
                ctk.CTkLabel(bc, text=f"[{b.severity}] {b.title}", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=6)
        else:
            ctk.CTkLabel(scroll, text="Açık hata bulunmuyor.", font=ctk.CTkFont(size=11), text_color="#64748b").pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(scroll, text="⚠️ Kod Tabanı Teknik Borçları (Technical Debt & TODOs)", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f59e0b").pack(anchor="w", pady=(14, 4))
        debts = self.project_service.db.list_tech_debt(self.current_project_id)
        for td in debts:
            dc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=8)
            dc.pack(fill="x", pady=3)
            ctk.CTkLabel(dc, text=f"• {td.title}", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=4)
            ctk.CTkLabel(dc, text=td.description, font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(anchor="w", padx=10, pady=(0, 4))

    # =========================================================================
    # TAB: DOKÜMANLAR & ADR
    # =========================================================================

    def _render_tab_docs_adr(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="🏛️ Mimari Karar Kayıtları (ADR)", font=ctk.CTkFont(size=13, weight="bold"), text_color="#38bdf8").pack(anchor="w", pady=(0, 6))
        adrs = self.project_service.db.list_adrs(self.current_project_id)
        for a in adrs:
            ac = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=8)
            ac.pack(fill="x", pady=3)
            ctk.CTkLabel(ac, text=f"{a.adr_number}: {a.title}", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=(6, 2))
            ctk.CTkLabel(ac, text=f"Karar: {a.decision}", font=ctk.CTkFont(size=10), text_color="#cbd5e1").pack(anchor="w", padx=10, pady=(0, 6))

    # =========================================================================
    # TAB: PROJE HAFIZASI (PROJECT MEMORY)
    # =========================================================================

    def _render_tab_memory(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="🧠 Proje Uzun Süreli Hafızası (Memory)", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", pady=(0, 6))

        mems = self.project_service.db.list_memories(self.current_project_id)
        for m in mems:
            mc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=8)
            mc.pack(fill="x", pady=3)
            pin_badge = "📌 [Sabit] " if m.status == "pinned" else ""
            ctk.CTkLabel(mc, text=f"{pin_badge}({m.memory_type}): {m.content}", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=10, pady=6)

    # =========================================================================
    # TAB: ARAMA & PROJEYE SOR (SEARCH & ASK)
    # =========================================================================

    def _render_tab_search_ask(self, container: Any) -> None:
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(container, text="🔍 Projede Ara & Projeye Sor", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").grid(row=0, column=0, sticky="w", pady=(0, 6))

        input_box = ctk.CTkFrame(container, fg_color="transparent")
        input_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        input_box.grid_columnconfigure(0, weight=1)

        search_input = ctk.CTkEntry(input_box, placeholder_text="Proje hakkında soru sorun (örn: 'JWT refresh nerede yapılıyor?') veya arayın...", height=34)
        search_input.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        res_box = ctk.CTkTextbox(container, font=ctk.CTkFont(size=12))
        res_box.grid(row=2, column=0, sticky="nsew")

        def run_search_or_ask():
            q = search_input.get().strip()
            if not q:
                return
            qa = self.project_service.coding.ask_project(self.current_project_id, q)
            res_box.delete("1.0", "end")
            res_box.insert("end", f"=== SORU: {q} ===\n\nKaynak Referansları & Alıntılar:\n")
            for c in qa["citations"]:
                res_box.insert("end", f"• [{c['type'].upper()}] {c['ref']} - {c['detail']}\n")
            res_box.insert("end", f"\nBağlam İstem Önizlemesi:\n{qa['context_prompt']}\n")

        ask_btn = ctk.CTkButton(input_box, text="Projeye Sor", width=90, height=34, command=run_search_or_ask)
        ask_btn.grid(row=0, column=1)

    # =========================================================================
    # TAB: AI PM & ÖNERİLER (AI PM & SUGGESTIONS)
    # =========================================================================

    def _render_tab_ai_pm(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="🛡️ AI Project Manager Kontrol Merkezi & Öneriler", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", pady=(0, 6))

        sugs = self.project_service.db.list_suggestions(self.current_project_id, status="pending")
        if sugs:
            for s in sugs:
                sc = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
                sc.pack(fill="x", pady=4)

                ctk.CTkLabel(sc, text=f"💡 {s.title}", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=12, pady=(10, 2))
                ctk.CTkLabel(sc, text=f"Kanıt: {s.evidence}", font=ctk.CTkFont(size=10), text_color="#38bdf8").pack(anchor="w", padx=12, pady=1)
                ctk.CTkLabel(sc, text=s.description, font=ctk.CTkFont(size=11), text_color=("gray30", "#cbd5e1")).pack(anchor="w", padx=12, pady=2)

                btn_row = ctk.CTkFrame(sc, fg_color="transparent")
                btn_row.pack(anchor="e", padx=12, pady=(4, 10))

                ctk.CTkButton(btn_row, text="Kabul Et", width=70, height=24, fg_color="#10b981", command=lambda sid=s.id: self._resolve_sug(sid, "accept")).pack(side="left", padx=2)
                ctk.CTkButton(btn_row, text="Reddet", width=70, height=24, fg_color="#ef4444", command=lambda sid=s.id: self._resolve_sug(sid, "reject")).pack(side="left", padx=2)
                ctk.CTkButton(btn_row, text="Yoksay", width=70, height=24, fg_color="transparent", border_width=1, command=lambda sid=s.id: self._resolve_sug(sid, "ignore")).pack(side="left", padx=2)
        else:
            ctk.CTkLabel(scroll, text="Bekleyen AI önerisi bulunmuyor. Proje durumu tutarlı.", font=ctk.CTkFont(size=11), text_color="#64748b").pack(pady=20)

    def _resolve_sug(self, sug_id: str, action: str) -> None:
        self.project_service.resolve_suggestion(sug_id, action)
        self._render_active_workspace_tab()

    # =========================================================================
    # TAB: AYARLAR (SETTINGS)
    # =========================================================================

    def _render_tab_settings(self, container: Any) -> None:
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(scroll, text="⚙️ Proje Ayarları & İzinler", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", pady=(0, 6))

        settings = self.project_service.db.get_settings(self.current_project_id)
        c = ctk.CTkFrame(scroll, fg_color=("gray92", "#161f2e"), corner_radius=10)
        c.pack(fill="x", pady=6)

        ctk.CTkLabel(c, text=f"Kodlama Modeli: {settings.coding_model}", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=4)
        ctk.CTkLabel(c, text=f"AI PM Modeli: {settings.pm_model}", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=4)
        ctk.CTkLabel(c, text=f"Otonomi Seviyesi: {settings.autonomy_level}", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=4)
        ctk.CTkLabel(c, text=f"Kontrol Sıklığı: {settings.pm_check_interval}", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=12, pady=4)

    # =========================================================================
    # EYLEM DİYALOGLARI (QUICK ADD, PM CHECK, PALETTE)
    # =========================================================================

    def _open_quick_add_menu(self) -> None:
        self._open_new_task_dialog()

    def _open_new_task_dialog(self) -> None:
        d = ctk.CTkToplevel(self)
        d.title("Yeni Görev Ekle")
        d.geometry("480x360")
        d.transient(self)

        ctk.CTkLabel(d, text="Görev Başlığı:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(16, 2))
        title_e = ctk.CTkEntry(d, placeholder_text="Örn: Login endpoint integration tests")
        title_e.pack(fill="x", padx=16, pady=4)

        ctk.CTkLabel(d, text="Açıklama:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(8, 2))
        desc_e = ctk.CTkEntry(d, placeholder_text="Görev detayları...")
        desc_e.pack(fill="x", padx=16, pady=4)

        def save():
            t_txt = title_e.get().strip()
            if t_txt:
                self.project_service.create_task(self.current_project_id, title=t_txt, description=desc_e.get().strip())
                d.destroy()
                self._render_active_workspace_tab()

        ctk.CTkButton(d, text="Kaydet", command=save).pack(pady=16)

    def _open_new_feature_dialog(self) -> None:
        d = ctk.CTkToplevel(self)
        d.title("Yeni Özellik Ekle")
        d.geometry("480x360")
        d.transient(self)

        ctk.CTkLabel(d, text="Özellik Başlığı:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(16, 2))
        title_e = ctk.CTkEntry(d, placeholder_text="Örn: Kullanıcı Kimlik Doğrulama")
        title_e.pack(fill="x", padx=16, pady=4)

        ctk.CTkLabel(d, text="Açıklama:", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=16, pady=(8, 2))
        desc_e = ctk.CTkEntry(d, placeholder_text="Özellik kapsamı...")
        desc_e.pack(fill="x", padx=16, pady=4)

        def save():
            t_txt = title_e.get().strip()
            if t_txt:
                self.project_service.create_feature(self.current_project_id, title=t_txt, description=desc_e.get().strip())
                d.destroy()
                self._render_active_workspace_tab()

        ctk.CTkButton(d, text="Kaydet", command=save).pack(pady=16)

    def _run_pm_check_action(self) -> None:
        try:
            check = self.project_service.pm.run_project_check(self.current_project_id, trigger="manual", force=True)
            messagebox.showinfo("AI PM Kontrolü Tamamlandı", check.summary)
            self._render_active_workspace_tab()
        except Exception as e:
            messagebox.showerror("Hata", f"Kontrol sırasında bir hata oluştu:\n{e}")

    def _open_command_palette(self) -> None:
        d = ctk.CTkToplevel(self)
        d.title("Komut Paleti (Command Palette)")
        d.geometry("440x300")
        d.transient(self)

        ctk.CTkLabel(d, text="⚡ Hızlı Eylemler", font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=16, pady=12)

        commands = [
            ("Yeni Görev Oluştur", lambda: [d.destroy(), self._open_new_task_dialog()]),
            ("Yeni Özellik Oluştur", lambda: [d.destroy(), self._open_new_feature_dialog()]),
            ("AI PM Kontrolünü Çalıştır", lambda: [d.destroy(), self._run_pm_check_action()]),
            ("Kodlama Ajanına Geç", lambda: [d.destroy(), self._switch_workspace_tab("coding")]),
            ("Projeye Soru Sor", lambda: [d.destroy(), self._switch_workspace_tab("search_ask")]),
        ]
        for cmd_name, cmd_fn in commands:
            b = ctk.CTkButton(d, text=cmd_name, anchor="w", fg_color="transparent", hover_color=("gray85", "#1e293b"), command=cmd_fn)
            b.pack(fill="x", padx=16, pady=2)

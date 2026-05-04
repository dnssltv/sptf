import threading
import traceback
import sys
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import requests
from PIL import Image

from spotify_import import (
    DEFAULT_INPUT_FILE,
    DEFAULT_REDIRECT_URI,
    check_spotify_status,
    import_tracks,
    load_saved_credentials,
    read_tracks,
)
from yandex_music_utils import fetch_playlist_tracks_from_url, save_tracks_txt

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")

SPOTIFY_BG = "#121212"
SPOTIFY_CARD = "#181818"
SPOTIFY_TEXT = "#FFFFFF"
SPOTIFY_SUBTLE = "#B3B3B3"
SPOTIFY_GREEN = "#1DB954"


def _safe_filename(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in text).strip()
    return (out or "playlist")[:80]


class SpotifyImporterGUI(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify Importer")
        self._try_set_window_icon()
        self.geometry("1040x760")
        self.minsize(960, 700)
        self.configure(fg_color=SPOTIFY_BG)

        self.source_url_var = ctk.StringVar()
        self.file_path_var = ctk.StringVar(value=DEFAULT_INPUT_FILE)
        self.playlist_name_var = ctk.StringVar(value="Импорт из Яндекс Музыки")
        self.public_var = ctk.BooleanVar(value=False)

        self.progress_var = ctk.DoubleVar(value=0.0)
        self.progress_label_var = ctk.StringVar(value="0% (0/0)")
        self.status_var = ctk.StringVar(value="Готово")

        self.tracks: list[str] = []
        self.source_file: Path | None = None
        self.spotify_status: dict | None = None
        self.current_step = 0
        self.session_path = Path.cwd() / "import_session.json"
        self.debug_log_path = Path.cwd() / "import_debug.log"

        self._is_running = False
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()

        self.step_titles = [
            "1. Источник треков",
            "2. Вход в Spotify",
            "3. Настройка плейлиста",
            "4. Предпросмотр",
            "5. Импорт",
        ]

        self._build_ui()
        self._show_step(0)


    def _try_set_window_icon(self) -> None:
        base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        icon_path = base_path / "spotify.ico"
        if not icon_path.exists():
            self._write_debug_line(f"[UI] Иконка не загружена: файл не найден ({icon_path})")
            return

        try:
            self.iconbitmap(default=str(icon_path))
        except Exception:
            try:
                self.wm_iconbitmap(str(icon_path))
            except Exception as exc:
                self._write_debug_line(f"[UI] Иконка не загружена: {exc}")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color=SPOTIFY_BG)
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top,
            text="Мастер импорта плейлиста",
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=SPOTIFY_TEXT,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            top,
            textvariable=self.status_var,
            text_color=SPOTIFY_SUBTLE,
            font=ctk.CTkFont(size=13),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.card = ctk.CTkFrame(self, fg_color=SPOTIFY_CARD, corner_radius=14)
        self.card.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self.card.grid_columnconfigure(0, weight=1)
        self.card.grid_rowconfigure(1, weight=1)

        self.step_header = ctk.CTkLabel(
            self.card, text="", font=ctk.CTkFont(size=20, weight="bold"), text_color=SPOTIFY_TEXT
        )
        self.step_header.grid(row=0, column=0, sticky="w", padx=20, pady=(16, 8))

        self.step_container = ctk.CTkFrame(self.card, fg_color="transparent")
        self.step_container.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 8))
        self.step_container.grid_columnconfigure(0, weight=1)
        self.step_container.grid_rowconfigure(0, weight=1)

        self.step_frames: list[ctk.CTkFrame] = []
        for builder in (
            self._build_step_source,
            self._build_step_login,
            self._build_step_playlist,
            self._build_step_review,
            self._build_step_import,
        ):
            frame = ctk.CTkFrame(self.step_container, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            builder(frame)
            self.step_frames.append(frame)

        nav = ctk.CTkFrame(self, fg_color=SPOTIFY_BG)
        nav.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 16))
        nav.grid_columnconfigure(1, weight=1)
        self.back_btn = ctk.CTkButton(nav, text="Назад", width=120, command=self._go_back, fg_color="#2a2a2a")
        self.back_btn.grid(row=0, column=0, sticky="w")
        self.next_btn = ctk.CTkButton(nav, text="Далее", width=150, command=self._go_next, fg_color=SPOTIFY_GREEN, text_color="#000000")
        self.next_btn.grid(row=0, column=2, sticky="e")

    def _build_step_source(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame,
            text="Вставьте ссылку на плейлист Яндекс Музыки или загрузите TXT-файл.",
            text_color=SPOTIFY_SUBTLE,
        ).grid(row=0, column=0, sticky="w", pady=(0, 12))

        ctk.CTkLabel(frame, text="Ссылка на плейлист").grid(row=1, column=0, sticky="w")
        ctk.CTkEntry(frame, textvariable=self.source_url_var, height=38).grid(row=2, column=0, sticky="ew", pady=(4, 10))

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.grid(row=3, column=0, sticky="w")
        ctk.CTkButton(row, text="Как получить ссылку", command=self._show_yandex_help, width=190, fg_color="#2a2a2a").pack(side="left")
        ctk.CTkButton(row, text="Загрузить треки по ссылке", command=self._start_fetch_from_link, width=220).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(frame, text="или выберите TXT").grid(row=4, column=0, sticky="w", pady=(18, 0))
        row2 = ctk.CTkFrame(frame, fg_color="transparent")
        row2.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        row2.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row2, textvariable=self.file_path_var, height=38).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(row2, text="Обзор...", command=self._browse_file, width=120).grid(row=0, column=1, padx=(8, 0))

        self.source_summary = ctk.CTkLabel(frame, text="", text_color=SPOTIFY_SUBTLE)
        self.source_summary.grid(row=6, column=0, sticky="w", pady=(12, 0))

    def _build_step_login(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame,
            text="Нажмите кнопку ниже, чтобы открыть браузер и войти в Spotify.",
            text_color=SPOTIFY_SUBTLE,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            frame,
            text="Войти в Spotify",
            command=self._start_spotify_login,
            fg_color=SPOTIFY_GREEN,
            text_color="#000000",
            width=220,
            height=42,
        ).grid(row=1, column=0, sticky="w", pady=(16, 0))
        profile_card = ctk.CTkFrame(frame, fg_color="#101010", corner_radius=12, height=120)
        profile_card.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        profile_card.grid_columnconfigure(1, weight=1)

        self.avatar_label = ctk.CTkLabel(
            profile_card,
            text="👤",
            width=84,
            height=84,
            fg_color="#2a2a2a",
            corner_radius=42,
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        self.avatar_label.grid(row=0, column=0, padx=16, pady=16)

        self.login_info_label = ctk.CTkLabel(
            profile_card,
            text="Вы еще не вошли.",
            text_color=SPOTIFY_SUBTLE,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.login_info_label.grid(row=0, column=1, sticky="w")

    def _build_step_playlist(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Название плейлиста").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(frame, textvariable=self.playlist_name_var, height=38).grid(row=1, column=0, sticky="ew", pady=(4, 10))
        ctk.CTkCheckBox(frame, text="Сделать плейлист публичным", variable=self.public_var).grid(row=2, column=0, sticky="w")

    def _build_step_review(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        stats_row = ctk.CTkFrame(frame, fg_color="transparent")
        stats_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        stats_row.grid_columnconfigure((0, 1, 2), weight=1)

        self.stat_tracks = ctk.CTkLabel(
            stats_row,
            text="Треков\n0",
            fg_color="#101010",
            corner_radius=12,
            justify="left",
            anchor="w",
            padx=16,
            pady=12,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.stat_tracks.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.stat_artists = ctk.CTkLabel(
            stats_row,
            text="Уникальных исполнителей\n0",
            fg_color="#101010",
            corner_radius=12,
            justify="left",
            anchor="w",
            padx=16,
            pady=12,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.stat_artists.grid(row=0, column=1, sticky="ew", padx=4)

        self.stat_unique_tracks = ctk.CTkLabel(
            stats_row,
            text="Уникальных строк\n0",
            fg_color="#101010",
            corner_radius=12,
            justify="left",
            anchor="w",
            padx=16,
            pady=12,
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.stat_unique_tracks.grid(row=0, column=2, sticky="ew", padx=(8, 0))

        self.review_title = ctk.CTkLabel(frame, text="Список треков", text_color=SPOTIFY_TEXT)
        self.review_title.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.review_box = ctk.CTkTextbox(frame, fg_color="#101010", text_color=SPOTIFY_TEXT)
        self.review_box.grid(row=2, column=0, sticky="nsew")

    def _build_step_import(self, frame: ctk.CTkFrame) -> None:
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Прогресс импорта").grid(row=0, column=0, sticky="w")
        self.progress = ctk.CTkProgressBar(frame, variable=self.progress_var, height=18)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        self.progress.set(0)
        ctk.CTkLabel(frame, textvariable=self.progress_label_var, text_color=SPOTIFY_SUBTLE).grid(row=2, column=0, sticky="w")

        control = ctk.CTkFrame(frame, fg_color="transparent")
        control.grid(row=3, column=0, sticky="w", pady=(10, 8))
        self.start_btn = ctk.CTkButton(control, text="Старт", command=lambda: self._start_import(resume=False), fg_color=SPOTIFY_GREEN, text_color="#000000")
        self.start_btn.pack(side="left")
        self.resume_btn = ctk.CTkButton(control, text="Продолжить после сбоя", command=lambda: self._start_import(resume=True), fg_color="#2a2a2a")
        self.resume_btn.pack(side="left", padx=(8, 0))
        self.pause_btn = ctk.CTkButton(control, text="Пауза", command=self._toggle_pause, state="disabled", fg_color="#2a2a2a")
        self.pause_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn = ctk.CTkButton(control, text="Остановить", command=self._cancel_import, state="disabled", fg_color="#2a2a2a")
        self.cancel_btn.pack(side="left", padx=(8, 0))

        self.log_box = ctk.CTkTextbox(frame, fg_color="#101010", text_color=SPOTIFY_TEXT, height=180)
        self.log_box.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        frame.grid_rowconfigure(4, weight=0)

    def _show_step(self, idx: int) -> None:
        self.current_step = idx
        self.step_header.configure(text=self.step_titles[idx])
        for i, frame in enumerate(self.step_frames):
            if i == idx:
                frame.grid()
            else:
                frame.grid_remove()
        self.back_btn.configure(state="normal" if idx > 0 else "disabled")
        self.next_btn.configure(state="disabled" if idx == len(self.step_frames) - 1 else "normal")
        if idx == len(self.step_frames) - 1:
            self._refresh_resume_button()

    def _go_back(self) -> None:
        if self.current_step > 0:
            self._show_step(self.current_step - 1)

    def _go_next(self) -> None:
        if self.current_step == 0:
            if not self._ensure_source_ready():
                return
        elif self.current_step == 1:
            if not self.spotify_status:
                messagebox.showwarning("Вход", "Сначала выполните вход в Spotify.")
                return
        elif self.current_step == 2:
            if not self.playlist_name_var.get().strip():
                messagebox.showwarning("Название", "Введите название плейлиста.")
                return
            self._refresh_review()
        if self.current_step < len(self.step_frames) - 1:
            self._show_step(self.current_step + 1)

    def _show_yandex_help(self) -> None:
        messagebox.showinfo(
            "Как получить ссылку",
            "1) Откройте плейлист в Яндекс Музыке.\n"
            "2) Нажмите Поделиться -> Скопировать ссылку.\n"
            "3) Вставьте ссылку вида:\n"
            "https://music.yandex.ru/users/<user>/playlists/<id>",
        )

    def _browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Выберите TXT",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if selected:
            self.file_path_var.set(selected)
            self.tracks = []
            self.source_file = None

    def _ensure_source_ready(self) -> bool:
        if self.tracks and self.source_file and self.source_file.exists():
            return True
        url = self.source_url_var.get().strip()
        if url:
            self._start_fetch_from_link()
            return False
        path = self.file_path_var.get().strip()
        if path and Path(path).exists():
            self._load_tracks_from_file(Path(path))
            return True
        messagebox.showwarning("Источник", "Вставьте ссылку Яндекс Музыки или выберите TXT.")
        return False

    def _load_tracks_from_file(self, path: Path) -> None:
        parsed = read_tracks(path)
        if not parsed:
            raise RuntimeError("В выбранном файле нет валидных треков.")
        self.tracks = [item.raw for item in parsed]
        self.source_file = path
        self.source_summary.configure(text=f"Загружено: {len(self.tracks)} треков")
        self.status_var.set(f"Источник готов: {len(self.tracks)} треков")

    def _start_fetch_from_link(self) -> None:
        url = self.source_url_var.get().strip()
        if not url:
            messagebox.showwarning("Ссылка", "Вставьте ссылку на плейлист.")
            return
        self.status_var.set("Загрузка треков из Яндекс Музыки...")

        def worker() -> None:
            try:
                data = fetch_playlist_tracks_from_url(url, log=self._log)
                stamp = datetime.now().strftime("%Y%m%d_%H%M")
                output = Path.cwd() / f"{_safe_filename(str(data['title']))}_{stamp}.txt"
                save_tracks_txt(output, data["tracks"])
                self.after(0, lambda: self.file_path_var.set(str(output)))
                self.after(0, lambda: self._load_tracks_from_file(output))
                self.after(0, lambda: self.status_var.set("Источник готов"))
            except Exception as exc:
                self.after(0, lambda e=exc: self._show_error("Ошибка загрузки из Яндекс Музыки", e))

        threading.Thread(target=worker, daemon=True).start()

    def _get_credentials(self) -> dict | None:
        creds = load_saved_credentials()
        if not creds.get("client_id") or not creds.get("client_secret"):
            messagebox.showerror(
                "Конфигурация",
                "Приложение не настроено: отсутствуют Spotify API ключи (.env).",
            )
            return None
        return {
            "client_id": creds.get("client_id", ""),
            "client_secret": creds.get("client_secret", ""),
            "redirect_uri": creds.get("redirect_uri", DEFAULT_REDIRECT_URI) or DEFAULT_REDIRECT_URI,
        }

    def _start_spotify_login(self) -> None:
        creds = self._get_credentials()
        if not creds:
            return
        self.status_var.set("Открываю вход в Spotify...")

        def worker() -> None:
            try:
                status = check_spotify_status(
                    credentials=creds,
                    interactive_credentials=False,
                    force_reauth=True,
                    log=self._log,
                )
                self.spotify_status = status
                self.after(0, lambda: self._render_profile_card(status))
                self.after(
                    0,
                    lambda: self.login_info_label.configure(
                        text=f"{status['display_name']}",
                        text_color="#82E0AA",
                    ),
                )
                self.after(0, lambda: self.status_var.set("Вход в Spotify выполнен"))
            except Exception as exc:
                self.after(0, lambda e=exc: self._show_error("Ошибка входа в Spotify", e))

        threading.Thread(target=worker, daemon=True).start()

    def _render_profile_card(self, status: dict) -> None:
        image_url = (status.get("image_url") or "").strip()
        if not image_url:
            return
        try:
            resp = requests.get(image_url, timeout=10)
            resp.raise_for_status()
            from io import BytesIO

            pil_image = Image.open(BytesIO(resp.content)).convert("RGB").resize((84, 84))
            avatar = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(84, 84))
            self.avatar_label.configure(image=avatar, text="")
            self.avatar_label.image = avatar
        except Exception:
            # Keep fallback placeholder on avatar loading errors.
            return

    def _refresh_review(self) -> None:
        tracks_count = len(self.tracks)
        unique_lines = len(set(self.tracks))
        artists = set()
        for item in self.tracks:
            left = item.split(" - ", 1)[0] if " - " in item else item
            for a in left.split(","):
                name = a.strip().lower()
                if name:
                    artists.add(name)

        self.stat_tracks.configure(text=f"Треков\n{tracks_count}")
        self.stat_artists.configure(text=f"Уникальных исполнителей\n{len(artists)}")
        self.stat_unique_tracks.configure(text=f"Уникальных строк\n{unique_lines}")
        self.review_box.delete("1.0", "end")
        for track in self.tracks:
            self.review_box.insert("end", f"{track}\n")

    def _refresh_resume_button(self) -> None:
        self.resume_btn.configure(state="normal" if self.session_path.exists() else "disabled")

    def _start_import(self, resume: bool) -> None:
        if self._is_running:
            return
        creds = self._get_credentials()
        if not creds:
            return
        if not self.spotify_status:
            messagebox.showwarning("Вход", "Сначала выполните вход в Spotify.")
            return

        if resume:
            if not self.session_path.exists():
                messagebox.showinfo("Продолжение", "Файл сессии не найден.")
                return
            if not self.source_file or not self.source_file.exists():
                candidate = self.file_path_var.get().strip()
                if candidate and Path(candidate).exists():
                    self.source_file = Path(candidate)
            if not self.source_file:
                messagebox.showwarning("Источник", "Укажите исходный TXT-файл для продолжения.")
                return
        else:
            if not self.source_file or not self.source_file.exists():
                messagebox.showwarning("Источник", "Сначала подготовьте источник треков.")
                return
            if self.session_path.exists():
                try:
                    self.session_path.unlink()
                except OSError:
                    pass

        name = self.playlist_name_var.get().strip()
        if not name:
            messagebox.showwarning("Название", "Введите название плейлиста.")
            return

        self._is_running = True
        self._pause_event.clear()
        self._cancel_event.clear()
        self.start_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Пауза")
        self.cancel_btn.configure(state="normal")
        self.progress_var.set(0)
        self.progress_label_var.set("0% (0/0)")
        self.log_box.delete("1.0", "end")
        self.status_var.set("Импорт выполняется...")

        def paused() -> bool:
            return self._pause_event.is_set()

        def cancelled() -> bool:
            return self._cancel_event.is_set()

        def progress_cb(data: dict) -> None:
            processed = int(data.get("processed", 0))
            total = int(data.get("total", 0))
            pct = 0 if total <= 0 else int((processed / total) * 100)
            self.after(0, lambda p=pct: self.progress_var.set(p / 100.0))
            self.after(0, lambda p=pct, pr=processed, tt=total: self.progress_label_var.set(f"{p}% ({pr}/{tt})"))

        def worker() -> None:
            try:
                result = import_tracks(
                    input_file=self.source_file,
                    playlist_name=name,
                    public=self.public_var.get(),
                    credentials=creds,
                    interactive_credentials=False,
                    force_reauth=False,
                    flush_batch_size=100,
                    is_paused=paused,
                    is_cancelled=cancelled,
                    on_progress=progress_cb,
                    state_path=self.session_path,
                    resume=resume,
                    verbose_track_log=True,
                    log=self._log,
                )
                state = "Остановлено пользователем" if result.get("cancelled") else "Готово"
                msg = (
                    f"{state}\n"
                    f"Обработано: {result['processed']}/{result['total']}\n"
                    f"Добавлено: {result['added']}\n"
                    f"Не найдено: {result['not_found']}\n\n"
                    f"Плейлист: {result['playlist_url']}"
                )
                self.after(0, lambda: messagebox.showinfo("Результат импорта", msg))
            except Exception as exc:
                self.after(0, lambda e=exc: self._show_error("Ошибка импорта", e))
            finally:
                self.after(0, self._finish_import_state)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_import_state(self) -> None:
        self._is_running = False
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled", text="Пауза")
        self.cancel_btn.configure(state="disabled")
        self.status_var.set("Готово")
        self._refresh_resume_button()

    def _toggle_pause(self) -> None:
        if not self._is_running:
            return
        if self._pause_event.is_set():
            self._pause_event.clear()
            self.pause_btn.configure(text="Пауза")
            self.status_var.set("Импорт выполняется...")
            self._log("Продолжено")
        else:
            self._pause_event.set()
            self.pause_btn.configure(text="Продолжить")
            self.status_var.set("Пауза")
            self._log("Пауза")

    def _cancel_import(self) -> None:
        if self._is_running:
            self._cancel_event.set()
            self._pause_event.clear()
            self.pause_btn.configure(text="Пауза")
            self.status_var.set("Остановка...")
            self._log("Запрошена остановка...")

    def _log(self, text: str) -> None:
        self._write_debug_line(text)
        self.after(0, lambda: self._append_log(text))

    def _append_log(self, text: str) -> None:
        # Hide low-level API/noise lines for end users.
        lowered = text.lower()
        if lowered.startswith("authorized as:") or lowered.startswith("авторизован:"):
            return
        if lowered.startswith("account product:") or lowered.startswith("тип аккаунта:"):
            return
        if "create playlist via /users/{id}/playlists was forbidden" in lowered:
            return
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _write_debug_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.debug_log_path.open("a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
        except OSError:
            pass

    def _show_error(self, title: str, exc: Exception) -> None:
        msg = str(exc).strip() or repr(exc) or "Неизвестная ошибка"
        self._append_log(f"[Ошибка] {title}: {msg}")
        self._write_debug_line(f"[Ошибка] {title}: {msg}")
        trace = traceback.format_exc()
        if trace.strip() and trace.strip() != "NoneType: None":
            self._append_log(trace)
            self._write_debug_line(trace)
        messagebox.showerror(title, msg)


def main() -> None:
    app = SpotifyImporterGUI()
    app.mainloop()


if __name__ == "__main__":
    main()

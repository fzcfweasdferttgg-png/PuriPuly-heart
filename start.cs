using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

class Launcher
{
    // --- Job Object: kill child processes when parent exits ---
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string name);

    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr hJob, int infoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public int LimitFlags;
        public IntPtr MinimumWorkingSetSize;
        public IntPtr MaximumWorkingSetSize;
        public int ActiveProcessLimit;
        public long Affinity;
        public int PriorityClass;
        public int SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public IntPtr ProcessMemoryLimit;
        public IntPtr JobMemoryLimit;
        public IntPtr PeakProcessMemoryUsed;
        public IntPtr PeakJobMemoryUsed;
    }

    const int JobObjectExtendedLimitInformation = 9;
    const int JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    static IntPtr jobHandle;

    static void SetupKillOnClose()
    {
        jobHandle = CreateJobObject(IntPtr.Zero, null);
        if (jobHandle == IntPtr.Zero) return;

        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr ptr = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(info, ptr, false);
            SetInformationJobObject(jobHandle, JobObjectExtendedLimitInformation, ptr, (uint)size);
        }
        finally { Marshal.FreeHGlobal(ptr); }
    }

    static void AssignToJob(IntPtr processHandle)
    {
        if (jobHandle != IntPtr.Zero)
            AssignProcessToJobObject(jobHandle, processHandle);
    }
    // --- Theme detection ---
    static bool IsDarkTheme()
    {
        try
        {
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))
            {
                if (key != null)
                {
                    object val = key.GetValue("AppsUseLightTheme");
                    if (val is int) return (int)val == 0;
                }
            }
        }
        catch { }
        return true; // default dark
    }

    // Color schemes
    static Color BG, BG2, BG3, FG, FG2, FG3, ACCENT_PINK, ACCENT_GREEN, ACCENT_BLUE, ACCENT_YELLOW, ACCENT_RED;

    static void InitColors(bool dark)
    {
        if (dark)
        {
            BG = Color.FromArgb(28, 28, 32);
            BG2 = Color.FromArgb(36, 36, 40);
            BG3 = Color.FromArgb(44, 44, 48);
            FG = Color.FromArgb(230, 230, 230);
            FG2 = Color.FromArgb(170, 170, 178);
            FG3 = Color.FromArgb(120, 120, 128);
            ACCENT_PINK = Color.FromArgb(240, 128, 180);
            ACCENT_GREEN = Color.FromArgb(100, 230, 140);
            ACCENT_BLUE = Color.FromArgb(130, 180, 255);
            ACCENT_YELLOW = Color.FromArgb(255, 200, 80);
            ACCENT_RED = Color.FromArgb(255, 120, 120);
        }
        else
        {
            BG = Color.FromArgb(242, 242, 246);
            BG2 = Color.FromArgb(230, 230, 236);
            BG3 = Color.FromArgb(218, 218, 226);
            FG = Color.FromArgb(30, 30, 30);
            FG2 = Color.FromArgb(80, 80, 88);
            FG3 = Color.FromArgb(120, 120, 128);
            ACCENT_PINK = Color.FromArgb(200, 60, 120);
            ACCENT_GREEN = Color.FromArgb(40, 160, 80);
            ACCENT_BLUE = Color.FromArgb(50, 100, 200);
            ACCENT_YELLOW = Color.FromArgb(180, 130, 20);
            ACCENT_RED = Color.FromArgb(200, 50, 50);
        }
    }

    static readonly string[][] MODELS = {
        new[] { "qwen3-asr-0.6b-int8-sherpa", "Qwen3 ASR 0.6B INT8", "https://huggingface.co/csukuangfj2/sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/resolve/2cc50d1abfe4d4f2df8d71f536d108bb40f943d2/" },
        new[] { "qwen3-asr-1.7b-int8-sherpa", "Qwen3 ASR 1.7B INT8", "https://huggingface.co/ilmina/qwen3-asr-1.7b-sherpa-onnx/resolve/main/" },
    };
    static readonly string[] REVISIONS = { "2cc50d1abfe4d4f2df8d71f536d108bb40f943d2", "main" };
    static readonly string[] FILES = { "conv_frontend.onnx", "decoder.int8.onnx", "encoder.int8.onnx", "tokenizer/merges.txt", "tokenizer/tokenizer_config.json", "tokenizer/vocab.json" };
    static readonly long[][] SIZES = {
        new long[] { 44148281, 756563239, 182491662, 1671853, 12487, 2776833 },
        new long[] { 48080441, 2037458645, 314222162, 1671853, 12487, 2776833 },
    };
    static readonly uint[][] CRC32S = {
        new uint[] { 0xCFA85A63, 0x0AD0DA54, 0xD7BCF214, 0x42998796, 0x1DCD864D, 0x8A9480F3 },
        new uint[] { 0x6AD3A6A9, 0xCE2DCC4E, 0xC40229D8, 0x42998796, 0x1DCD864D, 0x8A9480F3 },
    };

    static uint[] crcTable;
    static void InitCrcTable()
    {
        if (crcTable != null) return;
        crcTable = new uint[256];
        for (uint i = 0; i < 256; i++)
        {
            uint c = i;
            for (int j = 0; j < 8; j++)
                c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
            crcTable[i] = c;
        }
    }

    static uint ComputeCRC32(string filePath)
    {
        InitCrcTable();
        uint crc = 0xFFFFFFFF;
        using (var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            byte[] buf = new byte[65536];
            int bytesRead;
            while ((bytesRead = fs.Read(buf, 0, buf.Length)) > 0)
                for (int i = 0; i < bytesRead; i++)
                    crc = crcTable[(crc ^ buf[i]) & 0xFF] ^ (crc >> 8);
        }
        return crc ^ 0xFFFFFFFF;
    }

    static string Lang;
    static string ModelsDir;

    static Dictionary<string, Dictionary<string, string>> BuildTranslations()
    {
        var t = new Dictionary<string, Dictionary<string, string>>();

        t["ru"] = new Dictionary<string, string> { {"launch","Запустить"}, {"models","Модели"}, {"settings","Настройки"}, {"exit","Выход"}, {"download","Скачать"}, {"verify","Проверить"}, {"delete","Удалить"}, {"cancel","Отмена"}, {"not_installed","Не установлена"}, {"installed","Установлена"}, {"incomplete","Неполная"}, {"preparing","Подготовка..."}, {"all_ok","Все файлы в порядке!"}, {"files_bad","файлов отсутствует или повреждены"}, {"confirm_delete","Удалить модель?"}, {"dl_failed","Ошибка загрузки:"}, {"downloading","Загрузка"}, {"size","Размер"}, {"on_disk","на диске"}, {"models_title","Управление моделями"}, {"model_status","Статус"}, {"file_damaged","файл повреждён"}, {"file_incomplete","файл не докачан"}, {"file_not_found","файл не найден"}, {"files_ok","файлов в порядке"}, {"files_with_problems","файлов с проблемами"}, {"redownload_question","Перекачать эти файлы?"}, {"file_0","Аудио-процессор"}, {"file_1","Декодер (основной, ~750 МБ)"}, {"file_2","Энкодер"}, {"file_3","Словарь слияний токенов"}, {"file_4","Настройки токенизатора"}, {"file_5","Словарь токенов"}, {"settings_title","Управление настройками"}, {"settings_backup","Сделать копию"}, {"settings_restore","Восстановить"}, {"settings_delete","Удалить настройки"}, {"settings_info","Файл настроек:"}, {"settings_no_file","Файл настроек не найден"}, {"settings_backup_ok","Копия сохранена"}, {"settings_restore_ok","Настройки восстановлены"}, {"settings_delete_confirm","Удалить все настройки программы?\nЭто действие нельзя отменить."}, {"settings_delete_ok","Настройки удалены"} };

        t["zh"] = new Dictionary<string, string> { {"launch","启动"}, {"models","模型"}, {"exit","退出"}, {"download","下载"}, {"verify","验证"}, {"delete","删除"}, {"cancel","取消"}, {"not_installed","未安装"}, {"installed","已安装"}, {"incomplete","不完整"}, {"preparing","准备中..."}, {"all_ok","所有文件验证通过！"}, {"files_bad","个文件缺失或损坏"}, {"confirm_delete","删除模型？"}, {"dl_failed","下载失败："}, {"downloading","下载中"}, {"size","大小"}, {"on_disk","已占用"}, {"models_title","模型管理"}, {"model_status","状态"}, {"file_damaged","文件损坏"}, {"file_incomplete","文件未下载完成"}, {"file_not_found","文件未找到"}, {"files_ok","个文件正常"}, {"files_with_problems","个文件有问题"}, {"redownload_question","重新下载这些文件？"} };

        t["de"] = new Dictionary<string, string> { {"launch","Starten"}, {"models","Modelle"}, {"exit","Beenden"}, {"download","Herunterladen"}, {"verify","Prüfen"}, {"delete","Löschen"}, {"cancel","Abbrechen"}, {"not_installed","Nicht installiert"}, {"installed","Installiert"}, {"incomplete","Unvollständig"}, {"preparing","Vorbereitung..."}, {"all_ok","Alle Dateien OK!"}, {"files_bad","Dateien fehlen oder sind beschädigt"}, {"confirm_delete","Modell löschen?"}, {"dl_failed","Download fehlgeschlagen:"}, {"downloading","Download"}, {"size","Größe"}, {"on_disk","auf Disk"}, {"models_title","Modellverwaltung"}, {"model_status","Status"}, {"file_damaged","Datei beschädigt"}, {"file_incomplete","Datei unvollständig"}, {"file_not_found","Datei nicht gefunden"}, {"files_ok","Dateien OK"}, {"files_with_problems","Dateien mit Problemen"}, {"redownload_question","Diese Dateien erneut herunterladen?"} };

        t["fr"] = new Dictionary<string, string> { {"launch","Lancer"}, {"models","Modèles"}, {"exit","Quitter"}, {"download","Télécharger"}, {"verify","Vérifier"}, {"delete","Supprimer"}, {"cancel","Annuler"}, {"not_installed","Non installé"}, {"installed","Installé"}, {"incomplete","Incomplet"}, {"preparing","Préparation..."}, {"all_ok","Tous les fichiers OK !"}, {"files_bad","fichiers manquants ou corrompus"}, {"confirm_delete","Supprimer le modèle ?"}, {"dl_failed","Échec du téléchargement :"}, {"downloading","Téléchargement"}, {"size","Taille"}, {"on_disk","sur le disque"}, {"models_title","Gestion des modèles"}, {"model_status","Statut"}, {"file_damaged","fichier endommagé"}, {"file_incomplete","fichier incomplet"}, {"file_not_found","fichier non trouvé"}, {"files_ok","fichiers OK"}, {"files_with_problems","fichiers avec problèmes"}, {"redownload_question","Re-télécharger ces fichiers ?"} };

        t["es"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Modelos"}, {"exit","Salir"}, {"download","Descargar"}, {"verify","Verificar"}, {"delete","Eliminar"}, {"cancel","Cancelar"}, {"not_installed","No instalado"}, {"installed","Instalado"}, {"incomplete","Incompleto"}, {"preparing","Preparando..."}, {"all_ok","¡Todos los archivos OK!"}, {"files_bad","archivos faltantes o dañados"}, {"confirm_delete","¿Eliminar modelo?"}, {"dl_failed","Error de descarga:"}, {"downloading","Descargando"}, {"size","Tamaño"}, {"on_disk","en disco"}, {"models_title","Gestión de modelos"}, {"model_status","Estado"}, {"file_damaged","archivo dañado"}, {"file_incomplete","archivo incompleto"}, {"file_not_found","archivo no encontrado"}, {"files_ok","archivos OK"}, {"files_with_problems","archivos con problemas"}, {"redownload_question","¿Volver a descargar estos archivos?"} };

        t["pt"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Modelos"}, {"exit","Sair"}, {"download","Baixar"}, {"verify","Verificar"}, {"delete","Excluir"}, {"cancel","Cancelar"}, {"not_installed","Não instalado"}, {"installed","Instalado"}, {"incomplete","Incompleto"}, {"preparing","Preparando..."}, {"all_ok","Todos os arquivos OK!"}, {"files_bad","arquivos ausentes ou corrompidos"}, {"confirm_delete","Excluir modelo?"}, {"dl_failed","Falha no download:"}, {"downloading","Baixando"}, {"size","Tamanho"}, {"on_disk","no disco"}, {"models_title","Gerenciamento de modelos"}, {"model_status","Status"} };

        t["ja"] = new Dictionary<string, string> { {"launch","起動"}, {"models","モデル"}, {"exit","終了"}, {"download","ダウンロード"}, {"verify","検証"}, {"delete","削除"}, {"cancel","キャンセル"}, {"not_installed","未インストール"}, {"installed","インストール済み"}, {"incomplete","不完全"}, {"preparing","準備中..."}, {"all_ok","すべてのファイルが正常です！"}, {"files_bad","ファイルが見つからないか破損しています"}, {"confirm_delete","モデルを削除しますか？"}, {"dl_failed","ダウンロードエラー："}, {"downloading","ダウンロード中"}, {"size","サイズ"}, {"on_disk","使用中"}, {"models_title","モデル管理"}, {"model_status","ステータス"} };

        t["ko"] = new Dictionary<string, string> { {"launch","실행"}, {"models","모델"}, {"exit","종료"}, {"download","다운로드"}, {"verify","검증"}, {"delete","삭제"}, {"cancel","취소"}, {"not_installed","미설치"}, {"installed","설치됨"}, {"incomplete","불완전"}, {"preparing","준비 중..."}, {"all_ok","모든 파일이 정상입니다!"}, {"files_bad","파일이 없거나 손상됨"}, {"confirm_delete","모델을 삭제하시겠습니까?"}, {"dl_failed","다운로드 실패:"}, {"downloading","다운로드 중"}, {"size","크기"}, {"on_disk","디스크 사용"}, {"models_title","모델 관리"}, {"model_status","상태"} };

        t["ar"] = new Dictionary<string, string> { {"launch","تشغيل"}, {"models","نماذج"}, {"exit","خروج"}, {"download","تحميل"}, {"verify","تحقق"}, {"delete","حذف"}, {"cancel","إلغاء"}, {"not_installed","غير مثبت"}, {"installed","مثبت"}, {"incomplete","غير مكتمل"}, {"preparing","جاري التحضير..."}, {"all_ok","جميع الملفات سليمة!"}, {"files_bad","ملفات مفقودة أو تالفة"}, {"confirm_delete","حذف النموذج؟"}, {"dl_failed","فشل التحميل:"}, {"downloading","جاري التحميل"}, {"size","الحجم"}, {"on_disk","على القرص"}, {"models_title","إدارة النماذج"}, {"model_status","الحالة"} };

        t["tr"] = new Dictionary<string, string> { {"launch","Başlat"}, {"models","Modeller"}, {"exit","Çıkış"}, {"download","İndir"}, {"verify","Doğrula"}, {"delete","Sil"}, {"cancel","İptal"}, {"not_installed","Yüklü değil"}, {"installed","Yüklü"}, {"incomplete","Eksik"}, {"preparing","Hazırlanıyor..."}, {"all_ok","Tüm dosyalar tamam!"}, {"files_bad","dosya eksik veya bozuk"}, {"confirm_delete","Model silinsin mi?"}, {"dl_failed","İndirme hatası:"}, {"downloading","İndiriliyor"}, {"size","Boyut"}, {"on_disk","diskte"}, {"models_title","Model yönetimi"}, {"model_status","Durum"} };

        t["uk"] = new Dictionary<string, string> { {"launch","Запустити"}, {"models","Моделі"}, {"exit","Вихід"}, {"download","Завантажити"}, {"verify","Перевірити"}, {"delete","Видалити"}, {"cancel","Скасувати"}, {"not_installed","Не встановлено"}, {"installed","Встановлено"}, {"incomplete","Неповне"}, {"preparing","Підготовка..."}, {"all_ok","Усі файли в порядку!"}, {"files_bad","файлів відсутні або пошкоджені"}, {"confirm_delete","Видалити модель?"}, {"dl_failed","Помилка завантаження:"}, {"downloading","Завантаження"}, {"size","Розмір"}, {"on_disk","на диску"}, {"models_title","Управління моделями"}, {"model_status","Статус"} };

        t["vi"] = new Dictionary<string, string> { {"launch","Khởi chạy"}, {"models","Mô hình"}, {"exit","Thoát"}, {"download","Tải xuống"}, {"verify","Kiểm tra"}, {"delete","Xóa"}, {"cancel","Hủy"}, {"not_installed","Chưa cài đặt"}, {"installed","Đã cài"}, {"incomplete","Chưa đầy đủ"}, {"preparing","Đang chuẩn bị..."}, {"all_ok","Tất cả tệp đều OK!"}, {"files_bad","tệp bị thiếu hoặc lỗi"}, {"confirm_delete","Xóa mô hình?"}, {"dl_failed","Lỗi tải:"}, {"downloading","Đang tải"}, {"size","Kích thước"}, {"on_disk","trên ổ đĩa"}, {"models_title","Quản lý mô hình"}, {"model_status","Trạng thái"} };

        t["hi"] = new Dictionary<string, string> { {"launch","शुरू करें"}, {"models","मॉडल"}, {"exit","बाहर"}, {"download","डाउनलोड"}, {"verify","सत्यापित करें"}, {"delete","हटाएं"}, {"cancel","रद्द करें"}, {"not_installed","स्थापित नहीं"}, {"installed","स्थापित"}, {"incomplete","अपूर्ण"}, {"preparing","तैयारी हो रही है..."}, {"all_ok","सभी फ़ाइलें ठीक हैं!"}, {"files_bad","फ़ाइलें गुम या दूषित"}, {"confirm_delete","मॉडल हटाएं?"}, {"dl_failed","डाउनलोड विफल:"}, {"downloading","डाउनलोड हो रहा है"}, {"size","आकार"}, {"on_disk","डिस्क पर"}, {"models_title","मॉडल प्रबंधन"}, {"model_status","स्थिति"} };

        t["pl"] = new Dictionary<string, string> { {"launch","Uruchom"}, {"models","Modele"}, {"exit","Wyjdź"}, {"download","Pobierz"}, {"verify","Sprawdź"}, {"delete","Usuń"}, {"cancel","Anuluj"}, {"not_installed","Nie zainstalowano"}, {"installed","Zainstalowano"}, {"incomplete","Niekompletne"}, {"preparing","Przygotowywanie..."}, {"all_ok","Wszystkie pliki OK!"}, {"files_bad","plików brakuje lub uszkodzonych"}, {"confirm_delete","Usunąć model?"}, {"dl_failed","Błąd pobierania:"}, {"downloading","Pobieranie"}, {"size","Rozmiar"}, {"on_disk","na dysku"}, {"models_title","Zarządzanie modelami"}, {"model_status","Status"} };

        t["nl"] = new Dictionary<string, string> { {"launch","Starten"}, {"models","Modellen"}, {"exit","Afsluiten"}, {"download","Downloaden"}, {"verify","Controleren"}, {"delete","Verwijderen"}, {"cancel","Annuleren"}, {"not_installed","Niet geïnstalleerd"}, {"installed","Geïnstalleerd"}, {"incomplete","Onvolledig"}, {"preparing","Voorbereiden..."}, {"all_ok","Alle bestanden OK!"}, {"files_bad","bestanden ontbreken of beschadigd"}, {"confirm_delete","Model verwijderen?"}, {"dl_failed","Download mislukt:"}, {"downloading","Downloaden"}, {"size","Grootte"}, {"on_disk","op schijf"}, {"models_title","Modelbeheer"}, {"model_status","Status"} };

        t["it"] = new Dictionary<string, string> { {"launch","Avvia"}, {"models","Modelli"}, {"exit","Esci"}, {"download","Scarica"}, {"verify","Verifica"}, {"delete","Elimina"}, {"cancel","Annulla"}, {"not_installed","Non installato"}, {"installed","Installato"}, {"incomplete","Incompleto"}, {"preparing","Preparazione..."}, {"all_ok","Tutti i file OK!"}, {"files_bad","file mancanti o danneggiati"}, {"confirm_delete","Eliminare il modello?"}, {"dl_failed","Download fallito:"}, {"downloading","Download in corso"}, {"size","Dimensione"}, {"on_disk","su disco"}, {"models_title","Gestione modelli"}, {"model_status","Stato"} };

        t["sv"] = new Dictionary<string, string> { {"launch","Starta"}, {"models","Modeller"}, {"exit","Avsluta"}, {"download","Ladda ner"}, {"verify","Verifiera"}, {"delete","Ta bort"}, {"cancel","Avbryt"}, {"not_installed","Inte installerad"}, {"installed","Installerad"}, {"incomplete","Ofullständig"}, {"preparing","Förbereder..."}, {"all_ok","Alla filer OK!"}, {"files_bad","filer saknas eller skadade"}, {"confirm_delete","Ta bort modell?"}, {"dl_failed","Nedladdning misslyckades:"}, {"downloading","Laddar ner"}, {"size","Storlek"}, {"on_disk","på disk"}, {"models_title","Modellhantering"}, {"model_status","Status"} };

        t["da"] = new Dictionary<string, string> { {"launch","Start"}, {"models","Modeller"}, {"exit","Afslut"}, {"download","Download"}, {"verify","Bekræft"}, {"delete","Slet"}, {"cancel","Annuller"}, {"not_installed","Ikke installeret"}, {"installed","Installeret"}, {"incomplete","Ufuldstændig"}, {"preparing","Forbereder..."}, {"all_ok","Alle filer OK!"}, {"files_bad","filer mangler eller beskadigede"}, {"confirm_delete","Slette model?"}, {"dl_failed","Download fejlede:"}, {"downloading","Downloader"}, {"size","Størrelse"}, {"on_disk","på disk"}, {"models_title","Modeladministration"}, {"model_status","Status"} };

        t["fi"] = new Dictionary<string, string> { {"launch","Käynnistä"}, {"models","Mallit"}, {"exit","Lopeta"}, {"download","Lataa"}, {"verify","Vahvista"}, {"delete","Poista"}, {"cancel","Peruuta"}, {"not_installed","Ei asennettu"}, {"installed","Asennettu"}, {"incomplete","Epätäydellinen"}, {"preparing","Valmistellaan..."}, {"all_ok","Kaikki tiedostot OK!"}, {"files_bad","tiedostot puuttuvat tai vaurioituneita"}, {"confirm_delete","Poista malli?"}, {"dl_failed","Lataus epäonnistui:"}, {"downloading","Ladataan"}, {"size","Koko"}, {"on_disk","levyllä"}, {"models_title","Hallinta"}, {"model_status","Tila"} };

        t["no"] = new Dictionary<string, string> { {"launch","Start"}, {"models","Modeller"}, {"exit","Avslutt"}, {"download","Last ned"}, {"verify","Bekreft"}, {"delete","Slett"}, {"cancel","Avbryt"}, {"not_installed","Ikke installert"}, {"installed","Installert"}, {"incomplete","Ufullstendig"}, {"preparing","Forbereder..."}, {"all_ok","Alle filer OK!"}, {"files_bad","filer mangler eller skadet"}, {"confirm_delete","Slette modell?"}, {"dl_failed","Nedlasting feilet:"}, {"downloading","Laster ned"}, {"size","Størrelse"}, {"on_disk","på disk"}, {"models_title","Modelladministrasjon"}, {"model_status","Status"} };

        t["cs"] = new Dictionary<string, string> { {"launch","Spustit"}, {"models","Modely"}, {"exit","Konec"}, {"download","Stáhnout"}, {"verify","Ověřit"}, {"delete","Smazat"}, {"cancel","Zrušit"}, {"not_installed","Nenainstalováno"}, {"installed","Nainstalováno"}, {"incomplete","Neúplné"}, {"preparing","Příprava..."}, {"all_ok","Všechny soubory OK!"}, {"files_bad","soubory chybí nebo poškozené"}, {"confirm_delete","Smazat model?"}, {"dl_failed","Stahování selhalo:"}, {"downloading","Stahování"}, {"size","Velikost"}, {"on_disk","na disku"}, {"models_title","Správa modelů"}, {"model_status","Stav"} };

        t["sk"] = new Dictionary<string, string> { {"launch","Spustiť"}, {"models","Modely"}, {"exit","Koniec"}, {"download","Stiahnuť"}, {"verify","Overiť"}, {"delete","Vymazať"}, {"cancel","Zrušiť"}, {"not_installed","Nenainštalované"}, {"installed","Nainštalované"}, {"incomplete","Neúplné"}, {"preparing","Príprava..."}, {"all_ok","Všetky súbory OK!"}, {"files_bad","súbory chýbajú alebo poškodené"}, {"confirm_delete","Vymazať model?"}, {"dl_failed","Sťahovanie zlyhalo:"}, {"downloading","Sťahovanie"}, {"size","Veľkosť"}, {"on_disk","na disku"}, {"models_title","Správa modelov"}, {"model_status","Stav"} };

        t["hu"] = new Dictionary<string, string> { {"launch","Indítás"}, {"models","Modellek"}, {"exit","Kilépés"}, {"download","Letöltés"}, {"verify","Ellenőrzés"}, {"delete","Törlés"}, {"cancel","Mégse"}, {"not_installed","Nincs telepítve"}, {"installed","Telepítve"}, {"incomplete","Hiányos"}, {"preparing","Előkészítés..."}, {"all_ok","Minden fájl OK!"}, {"files_bad","fájlok hiányoznak vagy sérültek"}, {"confirm_delete","Modell törlése?"}, {"dl_failed","Letöltés sikertelen:"}, {"downloading","Letöltés folyamatban"}, {"size","Méret"}, {"on_disk","a lemezen"}, {"models_title","Modellkezelés"}, {"model_status","Állapot"} };

        t["ro"] = new Dictionary<string, string> { {"launch","Lansează"}, {"models","Modele"}, {"exit","Ieșire"}, {"download","Descarcă"}, {"verify","Verifică"}, {"delete","Șterge"}, {"cancel","Anulează"}, {"not_installed","Neinstalat"}, {"installed","Instalat"}, {"incomplete","Incomplet"}, {"preparing","Se pregătește..."}, {"all_ok","Toate fișierele OK!"}, {"files_bad","fișiere lipsă sau corupte"}, {"confirm_delete","Șterge modelul?"}, {"dl_failed","Descărcare eșuată:"}, {"downloading","Se descarcă"}, {"size","Dimensiune"}, {"on_disk","pe disc"}, {"models_title","Gestionare modele"}, {"model_status","Stare"} };

        t["bg"] = new Dictionary<string, string> { {"launch","Стартирай"}, {"models","Модели"}, {"exit","Изход"}, {"download","Изтегли"}, {"verify","Провери"}, {"delete","Изтрий"}, {"cancel","Отказ"}, {"not_installed","Не е инсталиран"}, {"installed","Инсталиран"}, {"incomplete","Непълен"}, {"preparing","Подготовка..."}, {"all_ok","Всички файлове са OK!"}, {"files_bad","файлове липсват или са повредени"}, {"confirm_delete","Изтрий модела?"}, {"dl_failed","Грешка при изтегляне:"}, {"downloading","Изтегляне"}, {"size","Размер"}, {"on_disk","на диска"}, {"models_title","Управление на модели"}, {"model_status","Статус"} };

        t["el"] = new Dictionary<string, string> { {"launch","Εκκίνηση"}, {"models","Μοντέλα"}, {"exit","Έξοδος"}, {"download","Λήψη"}, {"verify","Επαλήθευση"}, {"delete","Διαγραφή"}, {"cancel","Ακύρωση"}, {"not_installed","Δεν εγκαταστάθηκε"}, {"installed","Εγκατεστημένο"}, {"incomplete","Ελλιπές"}, {"preparing","Προετοιμασία..."}, {"all_ok","Όλα τα αρχεία OK!"}, {"files_bad","αρχεία λείπουν ή κατεστραμμένα"}, {"confirm_delete","Διαγραφή μοντέλου;"}, {"dl_failed","Αποτυχία λήψης:"}, {"downloading","Λήψη"}, {"size","Μέγεθος"}, {"on_disk","στο δίσκο"}, {"models_title","Διαχείριση μοντέλων"}, {"model_status","Κατάσταση"} };

        t["th"] = new Dictionary<string, string> { {"launch","เริ่ม"}, {"models","โมเดล"}, {"exit","ออก"}, {"download","ดาวน์โหลด"}, {"verify","ตรวจสอบ"}, {"delete","ลบ"}, {"cancel","ยกเลิก"}, {"not_installed","ยังไม่ได้ติดตั้ง"}, {"installed","ติดตั้งแล้ว"}, {"incomplete","ไม่สมบูรณ์"}, {"preparing","กำลังเตรียม..."}, {"all_ok","ไฟล์ทั้งหมด OK!"}, {"files_bad","ไฟล์หายไปหรือเสียหาย"}, {"confirm_delete","ลบโมเดล?"}, {"dl_failed","ดาวน์โหลดล้มเหลว:"}, {"downloading","กำลังดาวน์โหลด"}, {"size","ขนาด"}, {"on_disk","บนดิสก์"}, {"models_title","จัดการโมเดล"}, {"model_status","สถานะ"} };

        t["id"] = new Dictionary<string, string> { {"launch","Jalankan"}, {"models","Model"}, {"exit","Keluar"}, {"download","Unduh"}, {"verify","Verifikasi"}, {"delete","Hapus"}, {"cancel","Batal"}, {"not_installed","Belum terinstal"}, {"installed","Terinstal"}, {"incomplete","Tidak lengkap"}, {"preparing","Mempersiapkan..."}, {"all_ok","Semua file OK!"}, {"files_bad","file hilang atau rusak"}, {"confirm_delete","Hapus model?"}, {"dl_failed","Gagal mengunduh:"}, {"downloading","Mengunduh"}, {"size","Ukuran"}, {"on_disk","di disk"}, {"models_title","Manajemen model"}, {"model_status","Status"} };

        t["ms"] = new Dictionary<string, string> { {"launch","Lancar"}, {"models","Model"}, {"exit","Keluar"}, {"download","Muat turun"}, {"verify","Sahkan"}, {"delete","Padam"}, {"cancel","Batal"}, {"not_installed","Belum dipasang"}, {"installed","Dipasang"}, {"incomplete","Tidak lengkap"}, {"preparing","Menyediakan..."}, {"all_ok","Semua fail OK!"}, {"files_bad","fail tiada atau rosak"}, {"confirm_delete","Padam model?"}, {"dl_failed","Muat turun gagal:"}, {"downloading","Memuat turun"}, {"size","Saiz"}, {"on_disk","pada cakera"}, {"models_title","Pengurusan model"}, {"model_status","Status"} };

        t["ca"] = new Dictionary<string, string> { {"launch","Iniciar"}, {"models","Models"}, {"exit","Sortir"}, {"download","Descarregar"}, {"verify","Verificar"}, {"delete","Eliminar"}, {"cancel","Cancel·lar"}, {"not_installed","No instal·lat"}, {"installed","Instal·lat"}, {"incomplete","Incomplet"}, {"preparing","Preparant..."}, {"all_ok","Tots els fitxers OK!"}, {"files_bad","fitxers mancats o danyats"}, {"confirm_delete","Eliminar el model?"}, {"dl_failed","Error de descàrrega:"}, {"downloading","Descarregant"}, {"size","Mida"}, {"on_disk","al disc"}, {"models_title","Gestió de models"}, {"model_status","Estat"} };

        t["et"] = new Dictionary<string, string> { {"launch","Käivita"}, {"models","Mudelid"}, {"exit","Välju"}, {"download","Laadi alla"}, {"verify","Kontrolli"}, {"delete","Kustuta"}, {"cancel","Tühista"}, {"not_installed","Paigaldamata"}, {"installed","Paigaldatud"}, {"incomplete","Puudulik"}, {"preparing","Ettevalmistus..."}, {"all_ok","Kõik failid OK!"}, {"files_bad","failid puuduvad või vigased"}, {"confirm_delete","Kustuta mudel?"}, {"dl_failed","Allalaadimine ebaõnnestus:"}, {"downloading","Allalaadimine"}, {"size","Suurus"}, {"on_disk","kettal"}, {"models_title","Mudelite haldus"}, {"model_status","Olek"} };

        t["lv"] = new Dictionary<string, string> { {"launch","Palaist"}, {"models","Modeļi"}, {"exit","Iziet"}, {"download","Lejupielādēt"}, {"verify","Pārbaudīt"}, {"delete","Dzēst"}, {"cancel","Atcelt"}, {"not_installed","Nav instalēts"}, {"installed","Instalēts"}, {"incomplete","Nepilnīgs"}, {"preparing","Sagatavošana..."}, {"all_ok","Visi faili OK!"}, {"files_bad","faili trūkst vai bojāti"}, {"confirm_delete","Dzēst modeli?"}, {"dl_failed","Lejupielāde neizdevās:"}, {"downloading","Lejupielāde"}, {"size","Izmērs"}, {"on_disk","uz diska"}, {"models_title","Modeļu pārvaldība"}, {"model_status","Statuss"} };

        t["lt"] = new Dictionary<string, string> { {"launch","Paleisti"}, {"models","Modeliai"}, {"exit","Išeiti"}, {"download","Atsisiųsti"}, {"verify","Patikrinti"}, {"delete","Ištrinti"}, {"cancel","Atšaukti"}, {"not_installed","Neįdiegta"}, {"installed","Įdiegta"}, {"incomplete","Nebaigtas"}, {"preparing","Ruošiama..."}, {"all_ok","Visi failai OK!"}, {"files_bad","failai trūksta arba sugadinti"}, {"confirm_delete","Ištrinti modelį?"}, {"dl_failed","Atsisiuntimas nepavyko:"}, {"downloading","Atsisiunčiama"}, {"size","Dydis"}, {"on_disk","diske"}, {"models_title","Modelių valdymas"}, {"model_status","Būsena"} };

        t["sl"] = new Dictionary<string, string> { {"launch","Zaženi"}, {"models","Modeli"}, {"exit","Izhod"}, {"download","Prenesi"}, {"verify","Preveri"}, {"delete","Izbriši"}, {"cancel","Prekliči"}, {"not_installed","Ni nameščeno"}, {"installed","Nameščeno"}, {"incomplete","Nepopolno"}, {"preparing","Pripravljam..."}, {"all_ok","Vse datoteke OK!"}, {"files_bad","datoteke manjkajo ali poškodovane"}, {"confirm_delete","Izbrisati model?"}, {"dl_failed","Prenos ni uspel:"}, {"downloading","Prenašanje"}, {"size","Velikost"}, {"on_disk","na disku"}, {"models_title","Upravljanje modelov"}, {"model_status","Stanje"} };

        return t;
    }

    static string T(string key)
    {
        var tr = BuildTranslations();
        if (Lang != null && tr.ContainsKey(Lang) && tr[Lang].ContainsKey(key))
            return tr[Lang][key];
        switch (key)
        {
            case "launch": return "Launch";
            case "models": return "Models";
            case "settings": return "Settings";
            case "exit": return "Exit";
            case "download": return "Download";
            case "verify": return "Verify";
            case "delete": return "Delete";
            case "cancel": return "Cancel";
            case "not_installed": return "Not installed";
            case "installed": return "Installed";
            case "incomplete": return "Incomplete";
            case "preparing": return "Preparing...";
            case "all_ok": return "All files OK!";
            case "files_bad": return "files missing or corrupted";
            case "confirm_delete": return "Delete model?";
            case "dl_failed": return "Download failed:";
            case "downloading": return "Downloading";
            case "size": return "Size";
            case "on_disk": return "on disk";
            case "models_title": return "Model Management";
            case "model_status": return "Status";
            case "file_damaged": return "file is damaged";
            case "file_incomplete": return "file is incomplete (interrupted download)";
            case "file_not_found": return "file not found";
            case "files_ok": return "files OK";
            case "files_with_problems": return "files with problems";
            case "redownload_question": return "Re-download these files?";
            case "file_0": return "Audio processor";
            case "file_1": return "Decoder (main, ~750 MB)";
            case "file_2": return "Encoder";
            case "file_3": return "Token merge vocabulary";
            case "file_4": return "Tokenizer settings";
            case "file_5": return "Token vocabulary";
            case "settings_title": return "Settings Management";
            case "settings_backup": return "Backup";
            case "settings_restore": return "Restore";
            case "settings_delete": return "Delete settings";
            case "settings_info": return "Settings file:";
            case "settings_no_file": return "Settings file not found";
            case "settings_backup_ok": return "Backup saved";
            case "settings_restore_ok": return "Settings restored";
            case "settings_delete_confirm": return "Delete all app settings?\nThis cannot be undone.";
            case "settings_delete_ok": return "Settings deleted";
            default: return key;
        }
    }

    static void DetectLanguage()
    {
        try
        {
            string name = CultureInfo.CurrentUICulture.Name.ToLowerInvariant();
            string two = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            var tr = BuildTranslations();
            if (tr.ContainsKey(name)) Lang = name;
            else if (tr.ContainsKey(two)) Lang = two;
            else if (name.StartsWith("zh")) Lang = "zh";
            else if (name.StartsWith("pt")) Lang = "pt";
            else Lang = two;
        }
        catch { Lang = "en"; }
    }

    // --- Model helpers ---
    enum ModelStatus { Missing, Incomplete, Ready }

    static void EnsureInstalledManifest(int mi)
    {
        string dir = Path.Combine(ModelsDir, MODELS[mi][0]);
        string manifestPath = Path.Combine(dir, "installed-manifest.json");
        if (File.Exists(manifestPath)) return;
        try
        {
            string json = "{\n"
                + "  \"manifest_version\": 1,\n"
                + "  \"model_id\": \"" + MODELS[mi][0] + "\",\n"
                + "  \"engine\": \"sherpa-onnx\",\n"
                + "  \"install_dirname\": \"" + MODELS[mi][0] + "\",\n"
                + "  \"selected_source\": \"huggingface\",\n"
                + "  \"selected_revision\": \"" + REVISIONS[mi] + "\"\n"
                + "}";
            File.WriteAllText(manifestPath, json);
        }
        catch { }
    }

    static long ModelTotalSize(int mi)
    {
        long t = 0;
        foreach (long s in SIZES[mi]) t += s;
        return t;
    }

    static ModelStatus CheckModel(int mi, out int foundFiles, out long foundSize)
    {
        foundFiles = 0;
        foundSize = 0;
        string dir = Path.Combine(ModelsDir, MODELS[mi][0]);
        if (!Directory.Exists(dir)) return ModelStatus.Missing;
        for (int i = 0; i < FILES.Length; i++)
        {
            string fp = Path.Combine(dir, FILES[i]);
            if (File.Exists(fp))
            {
                foundFiles++;
                try { foundSize += new FileInfo(fp).Length; }
                catch { foundSize += SIZES[mi][i]; }
            }
        }
        if (foundFiles == FILES.Length) return ModelStatus.Ready;
        if (foundFiles > 0) return ModelStatus.Incomplete;
        return ModelStatus.Missing;
    }

    static string ReadAppVersion(string appSrc)
    {
        try
        {
            string initPath = Path.Combine(appSrc, "puripuly_heart", "__init__.py");
            if (File.Exists(initPath))
            {
                foreach (string line in File.ReadAllLines(initPath))
                {
                    if (line.StartsWith("__version__"))
                    {
                        int q1 = line.IndexOf('"');
                        int q2 = line.IndexOf('"', q1 + 1);
                        if (q1 >= 0 && q2 > q1) return line.Substring(q1 + 1, q2 - q1 - 1);
                        q1 = line.IndexOf('\'');
                        q2 = line.IndexOf('\'', q1 + 1);
                        if (q1 >= 0 && q2 > q1) return line.Substring(q1 + 1, q2 - q1 - 1);
                    }
                }
            }
        }
        catch { }
        return "?.?.?";
    }

    static string FormatSize(long bytes)
    {
        if (bytes >= 1073741824L) return string.Format("{0:F1} GB", bytes / 1073741824.0);
        if (bytes >= 1048576L) return string.Format("{0:F0} MB", bytes / 1048576.0);
        return string.Format("{0:F0} KB", bytes / 1024.0);
    }

    // --- Main ---
    static int Main(string[] args)
    {
        DetectLanguage();
        SetupKillOnClose();
        InitColors(IsDarkTheme());
        ServicePointManager.SecurityProtocol = (SecurityProtocolType)3072 | SecurityProtocolType.Tls11 | SecurityProtocolType.Tls;

        string root = AppDomain.CurrentDomain.BaseDirectory;
        string dataDir = Path.Combine(root, "data");
        ModelsDir = Path.Combine(dataDir, "models");
        string pythonExe = Path.Combine(root, "python", "python.exe");
        string appSrc = Path.Combine(root, "app", "src");
        string mainPy = Path.Combine(appSrc, "puripuly_heart", "main.py");

        if (!File.Exists(pythonExe))
        {
            MessageBox.Show("python.exe not found in:\n" + Path.Combine(root, "python"),
                "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        if (!File.Exists(mainPy))
        {
            MessageBox.Show("main.py not found in:\n" + Path.Combine(appSrc, "puripuly_heart"),
                "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        Directory.CreateDirectory(dataDir);
        // Always create model directories
        for (int mi = 0; mi < MODELS.Length; mi++)
            Directory.CreateDirectory(Path.Combine(ModelsDir, MODELS[mi][0]));

        // Read app version
        string appVersion = ReadAppVersion(appSrc);
        const string launcherVersion = "1.0.0";

        // --- Main launcher window ---
        bool doLaunch = false;

        var form = new Form
        {
            Text = "PuriPuly Heart",
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterScreen,
            Size = new Size(360, 210),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        var titleLabel = new Label
        {
            Text = "\u2665  PuriPuly Heart",
            Font = new Font("Segoe UI", 15, FontStyle.Bold),
            ForeColor = ACCENT_PINK,
            AutoSize = false,
            Size = new Size(340, 30),
            Location = new Point(0, 12),
            TextAlign = ContentAlignment.MiddleCenter,
        };

        var versionLabel = new Label
        {
            Text = "app v" + appVersion + "  \u00B7  launcher v" + launcherVersion,
            Font = new Font("Segoe UI", 8),
            ForeColor = FG3,
            AutoSize = false,
            Size = new Size(340, 16),
            Location = new Point(0, 42),
            TextAlign = ContentAlignment.MiddleCenter,
        };

        // Separator line
        var sep = new Label { AutoSize = false, Size = new Size(320, 1), Location = new Point(20, 62), BackColor = FG3 };

        var btnLaunch = new Button
        {
            Text = "\u25B6  " + T("launch"),
            Font = new Font("Segoe UI", 11, FontStyle.Bold),
            Size = new Size(105, 46),
            Location = new Point(10, 75),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_GREEN,
        };
        btnLaunch.FlatAppearance.BorderColor = ACCENT_GREEN;

        var btnModels = new Button
        {
            Text = "\u2699  " + T("models"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(105, 46),
            Location = new Point(122, 75),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_BLUE,
        };
        btnModels.FlatAppearance.BorderColor = ACCENT_BLUE;

        var btnSettings = new Button
        {
            Text = "\u2692  " + T("settings"),
            Font = new Font("Segoe UI", 10),
            Size = new Size(105, 46),
            Location = new Point(234, 75),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG3,
            ForeColor = ACCENT_YELLOW,
        };
        btnSettings.FlatAppearance.BorderColor = ACCENT_YELLOW;

        var btnExit = new Button
        {
            Text = T("exit"),
            Font = new Font("Segoe UI", 9),
            Size = new Size(70, 26),
            Location = new Point(280, 135),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = FG3,
        };
        btnExit.FlatAppearance.BorderColor = FG3;

        btnLaunch.Click += (s, e) => { doLaunch = true; form.Close(); };
        btnModels.Click += (s, e) => { ShowModelsDialog(); };
        btnSettings.Click += (s, e) => { ShowSettingsDialog(dataDir); };
        btnExit.Click += (s, e) => { form.Close(); };

        form.Controls.AddRange(new Control[] { titleLabel, versionLabel, sep, btnLaunch, btnModels, btnSettings, btnExit });
        form.ShowDialog();

        if (!doLaunch) return 0;

        // Ensure installed-manifest.json for verified models
        Form verifySplash = null;
        Label verifyLabel = null;
        ProgressBar verifyBar = null;
        bool needsVerify = false;
        for (int i = 0; i < MODELS.Length; i++)
        {
            string mp = Path.Combine(ModelsDir, MODELS[i][0], "installed-manifest.json");
            if (!File.Exists(mp) && Directory.Exists(Path.Combine(ModelsDir, MODELS[i][0])))
            { needsVerify = true; break; }
        }
        if (needsVerify)
        {
            verifySplash = new Form { Text = "PuriPuly Heart", FormBorderStyle = FormBorderStyle.FixedSingle, StartPosition = FormStartPosition.CenterScreen, Size = new Size(400, 140), BackColor = BG, TopMost = true, ShowInTaskbar = false, ControlBox = false };
            verifyLabel = new Label { Text = "", Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(370, 22), Location = new Point(12, 15) };
            verifyBar = new ProgressBar { Location = new Point(12, 45), Size = new Size(370, 22), Style = ProgressBarStyle.Continuous };
            verifySplash.Controls.Add(verifyLabel);
            verifySplash.Controls.Add(verifyBar);
            verifySplash.Show();
            verifySplash.Refresh();
        }
        for (int i = 0; i < MODELS.Length; i++)
        {
            string manifestPath = Path.Combine(ModelsDir, MODELS[i][0], "installed-manifest.json");
            if (File.Exists(manifestPath)) continue;
            string dir = Path.Combine(ModelsDir, MODELS[i][0]);
            if (!Directory.Exists(dir)) continue;
            bool allOk = true;
            for (int fi = 0; fi < FILES.Length; fi++)
            {
                string fp = Path.Combine(dir, FILES[fi]);
                if (!File.Exists(fp)) { allOk = false; break; }
                long sz = new FileInfo(fp).Length;
                if (sz != SIZES[i][fi]) { allOk = false; break; }
                if (verifyLabel != null) { verifyLabel.Text = MODELS[i][1] + " \u2014 " + FILES[fi]; verifyLabel.Refresh(); }
                if (verifyBar != null) { verifyBar.Value = (int)((long)(fi + 1) * 100 / FILES.Length); verifyBar.Refresh(); }
                Application.DoEvents();
                if (ComputeCRC32(fp) != CRC32S[i][fi]) { allOk = false; break; }
            }
            if (allOk) EnsureInstalledManifest(i);
        }
        if (verifySplash != null) { verifySplash.Close(); verifySplash.Dispose(); }

        // --- Launch app ---
        string exeName = Path.GetFileNameWithoutExtension(System.Reflection.Assembly.GetExecutingAssembly().Location);
        bool isDebug = exeName.ToLowerInvariant().Contains("debug");

        Form splash = null;
        if (!isDebug)
        {
            splash = new Form { Text = "PuriPuly Heart", FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.CenterScreen, Size = new Size(320, 160), BackColor = BG, TopMost = true, ShowInTaskbar = false };
            splash.Controls.Add(new Label { Text = "PuriPuly Heart", Font = new Font("Segoe UI", 16, FontStyle.Bold), ForeColor = ACCENT_PINK, AutoSize = false, Size = new Size(320, 40), Location = new Point(0, 30), TextAlign = ContentAlignment.MiddleCenter });
            splash.Controls.Add(new Label { Text = "Loading...", Font = new Font("Segoe UI", 10), ForeColor = FG2, AutoSize = false, Size = new Size(320, 25), Location = new Point(0, 85), TextAlign = ContentAlignment.MiddleCenter });
            splash.Show();
            Application.DoEvents();
        }

        var psi = new ProcessStartInfo { FileName = pythonExe, UseShellExecute = false, CreateNoWindow = !isDebug, WorkingDirectory = root };
        foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
            psi.Environment[(string)entry.Key] = (string)entry.Value;
        psi.Environment["PURIPULY_HEART_DATA_DIR"] = dataDir;

        string cmdArgs = "\"" + mainPy + "\"";
        foreach (string arg in args) cmdArgs += " \"" + arg + "\"";
        psi.Arguments = cmdArgs;

        try
        {
            using (var proc = Process.Start(psi))
            {
                AssignToJob(proc.Handle);
                if (splash != null) { Thread.Sleep(2000); splash.Close(); splash.Dispose(); }
                proc.WaitForExit();
                return proc.ExitCode;
            }
        }
        catch (Exception ex)
        {
            if (splash != null) { splash.Close(); splash.Dispose(); }
            MessageBox.Show("Failed to start:\n" + ex.Message, "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    // --- Settings dialog ---
    static void ShowSettingsDialog(string dataDir)
    {
        string settingsPath = Path.Combine(dataDir, "settings.json");
        string backupPath = Path.Combine(dataDir, "settings.backup.json");

        var form = new Form
        {
            Text = "\u2692  " + T("settings_title"),
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(400, 200),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        var statusLabel = new Label
        {
            Text = "",
            Font = new Font("Segoe UI", 9),
            ForeColor = FG,
            AutoSize = false,
            Size = new Size(370, 40),
            Location = new Point(12, 15),
        };

        int bw = 115, bh = 36, gap = 8;
        int bx = 12;

        var btnBackup = new Button
        {
            Text = "\u2B07  " + T("settings_backup"),
            Font = new Font("Segoe UI", 9),
            Size = new Size(bw, bh),
            Location = new Point(bx, 70),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = Color.FromArgb(100, 220, 130),
        };
        btnBackup.FlatAppearance.BorderColor = ACCENT_GREEN;
        bx += bw + gap;

        var btnRestore = new Button
        {
            Text = "\u2B06  " + T("settings_restore"),
            Font = new Font("Segoe UI", 9),
            Size = new Size(bw, bh),
            Location = new Point(bx, 70),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = ACCENT_BLUE,
        };
        btnRestore.FlatAppearance.BorderColor = ACCENT_BLUE;
        bx += bw + gap;

        var btnDel = new Button
        {
            Text = "\u2716  " + T("settings_delete"),
            Font = new Font("Segoe UI", 9),
            Size = new Size(bw, bh),
            Location = new Point(bx, 70),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = ACCENT_RED,
        };
        btnDel.FlatAppearance.BorderColor = ACCENT_RED;

        var btnClose = new Button
        {
            Text = T("exit"),
            Font = new Font("Segoe UI", 9),
            Size = new Size(80, 28),
            Location = new Point(160, 120),
            FlatStyle = FlatStyle.Flat,
            BackColor = BG2,
            ForeColor = FG2,
        };
        btnClose.FlatAppearance.BorderColor = FG3;

        Action refresh = () =>
        {
            bool hasSettings = File.Exists(settingsPath);
            bool hasBackup = File.Exists(backupPath);
            string info = "";

            if (hasSettings)
            {
                long sz = new FileInfo(settingsPath).Length;
                info += "\u2705 settings.json  (" + FormatSize(sz) + ")";
            }
            else
            {
                info += "\u274C settings.json  (" + T("settings_no_file") + ")";
            }

            if (hasBackup)
            {
                long sz = new FileInfo(backupPath).Length;
                info += "\n\u2705 settings.backup.json  (" + FormatSize(sz) + ")";
            }
            else
            {
                info += "\n\u25CB settings.backup.json  (" + T("settings_no_file") + ")";
            }

            statusLabel.Text = info;

            btnBackup.Enabled = hasSettings;
            btnBackup.ForeColor = hasSettings ? ACCENT_GREEN : FG3;
            btnBackup.FlatAppearance.BorderColor = hasSettings ? ACCENT_GREEN : FG3;

            btnRestore.Enabled = hasBackup;
            btnRestore.ForeColor = hasBackup ? ACCENT_BLUE : FG3;
            btnRestore.FlatAppearance.BorderColor = hasBackup ? ACCENT_BLUE : FG3;

            btnDel.Enabled = hasSettings;
            btnDel.ForeColor = hasSettings ? ACCENT_RED : FG3;
            btnDel.FlatAppearance.BorderColor = hasSettings ? ACCENT_RED : FG3;
        };
        refresh();

        // Backup — copy settings.json → settings.backup.json
        btnBackup.Click += (s, e) =>
        {
            try
            {
                File.Copy(settingsPath, backupPath, true);
                MessageBox.Show(T("settings_backup_ok") + "\n" + backupPath, "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Information);
                refresh();
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        };

        // Restore — copy settings.backup.json → settings.json
        btnRestore.Click += (s, e) =>
        {
            try
            {
                File.Copy(backupPath, settingsPath, true);
                MessageBox.Show(T("settings_restore_ok"), "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Information);
                refresh();
            }
            catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
        };

        // Delete
        btnDel.Click += (s, e) =>
        {
            if (MessageBox.Show(T("settings_delete_confirm"), "PuriPuly Heart", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            {
                try
                {
                    File.Delete(settingsPath);
                    MessageBox.Show(T("settings_delete_ok"), "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    refresh();
                }
                catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            }
        };

        btnClose.Click += (s, e) => form.Close();

        form.Controls.AddRange(new Control[] { statusLabel, btnBackup, btnRestore, btnDel, btnClose });
        form.ShowDialog();
    }

    // --- Models management dialog ---
    static void ShowModelsDialog()
    {
        var form = new Form
        {
            Text = "\u2699  " + T("models_title"),
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(480, 85 + MODELS.Length * 115),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        Label[] statusLabels = new Label[MODELS.Length];
        Label[] detailLabels = new Label[MODELS.Length];
        Button[] dlButtons = new Button[MODELS.Length];
        Button[] verifyButtons = new Button[MODELS.Length];
        Button[] delButtons = new Button[MODELS.Length];

        for (int mi = 0; mi < MODELS.Length; mi++)
        {
            int idx = mi;
            int py = 10 + mi * 115;

            var panel = new Panel { Size = new Size(450, 106), Location = new Point(10, py), BackColor = BG2, BorderStyle = BorderStyle.None };

            long total = ModelTotalSize(mi);

            // Top row: model name + size
            var nameLabel = new Label { Text = MODELS[mi][1], Font = new Font("Segoe UI", 11, FontStyle.Bold), ForeColor = FG, AutoSize = true, Location = new Point(12, 8) };
            var sizeLabel = new Label { Text = "~" + FormatSize(total), Font = new Font("Segoe UI", 10, FontStyle.Bold), ForeColor = ACCENT_PINK, AutoSize = true, Location = new Point(370, 10) };

            // Middle row: status
            statusLabels[mi] = new Label { Text = "", Font = new Font("Segoe UI", 9), ForeColor = FG2, AutoSize = false, Size = new Size(320, 18), Location = new Point(12, 30) };
            detailLabels[mi] = new Label { Text = "", Font = new Font("Segoe UI", 8), ForeColor = FG2, AutoSize = false, Size = new Size(320, 18), Location = new Point(12, 48) };

            // Bottom row: action buttons — right-aligned, equal size
            int bw = 100, bh = 28, gap = 6;
            int bx = 12;

            dlButtons[mi] = new Button { Text = "\u2B07 " + T("download"), Font = new Font("Segoe UI", 8.5f), Size = new Size(bw, bh), Location = new Point(bx, 72), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = Color.FromArgb(100, 220, 130) };
            dlButtons[mi].FlatAppearance.BorderColor = ACCENT_GREEN;
            bx += bw + gap;

            verifyButtons[mi] = new Button { Text = "\u2714 " + T("verify"), Font = new Font("Segoe UI", 8.5f), Size = new Size(bw, bh), Location = new Point(bx, 72), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = ACCENT_BLUE };
            verifyButtons[mi].FlatAppearance.BorderColor = ACCENT_BLUE;
            bx += bw + gap;

            delButtons[mi] = new Button { Text = "\u2716 " + T("delete"), Font = new Font("Segoe UI", 8.5f), Size = new Size(bw, bh), Location = new Point(bx, 72), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = ACCENT_RED };
            delButtons[mi].FlatAppearance.BorderColor = ACCENT_RED;
            bx += bw + gap;

            // HuggingFace link — after buttons
            string repoUrl = MODELS[mi][2].Substring(0, MODELS[mi][2].IndexOf("/resolve/"));
            var hfLink = new LinkLabel { Text = "HuggingFace", Font = new Font("Segoe UI", 8.5f), AutoSize = true, Location = new Point(bx, 76), LinkColor = ACCENT_YELLOW };
            hfLink.Click += (s, e) => { try { Process.Start(repoUrl); } catch { } };

            panel.Controls.AddRange(new Control[] { nameLabel, sizeLabel, statusLabels[mi], detailLabels[mi], dlButtons[mi], verifyButtons[mi], delButtons[mi], hfLink });
            form.Controls.Add(panel);

            // Refresh status
            Action refresh = () =>
            {
                int found; long fsize;
                var st = CheckModel(idx, out found, out fsize);
                switch (st)
                {
                    case ModelStatus.Ready:
                        statusLabels[idx].Text = "\u2705 " + T("installed") + " (" + found + "/" + FILES.Length + ")";
                        statusLabels[idx].ForeColor = ACCENT_GREEN;
                        detailLabels[idx].Text = FormatSize(fsize) + " " + T("on_disk");
                        dlButtons[idx].Enabled = false;
                        dlButtons[idx].ForeColor = FG3;
                        dlButtons[idx].FlatAppearance.BorderColor = FG3;
                        verifyButtons[idx].Enabled = true;
                        verifyButtons[idx].ForeColor = ACCENT_BLUE;
                        verifyButtons[idx].FlatAppearance.BorderColor = ACCENT_BLUE;
                        delButtons[idx].Enabled = true;
                        delButtons[idx].ForeColor = ACCENT_RED;
                        delButtons[idx].FlatAppearance.BorderColor = ACCENT_RED;
                        break;
                    case ModelStatus.Incomplete:
                        statusLabels[idx].Text = "\u26A0\uFE0F " + T("incomplete") + " (" + found + "/" + FILES.Length + ")";
                        statusLabels[idx].ForeColor = ACCENT_YELLOW;
                        detailLabels[idx].Text = FormatSize(fsize) + " / ~" + FormatSize(ModelTotalSize(idx));
                        dlButtons[idx].Enabled = true;
                        dlButtons[idx].ForeColor = ACCENT_GREEN;
                        dlButtons[idx].FlatAppearance.BorderColor = ACCENT_GREEN;
                        verifyButtons[idx].Enabled = true;
                        verifyButtons[idx].ForeColor = ACCENT_BLUE;
                        verifyButtons[idx].FlatAppearance.BorderColor = ACCENT_BLUE;
                        delButtons[idx].Enabled = true;
                        delButtons[idx].ForeColor = ACCENT_RED;
                        delButtons[idx].FlatAppearance.BorderColor = ACCENT_RED;
                        break;
                    case ModelStatus.Missing:
                        statusLabels[idx].Text = "\u274C " + T("not_installed");
                        statusLabels[idx].ForeColor = ACCENT_RED;
                        detailLabels[idx].Text = "";
                        dlButtons[idx].Enabled = true;
                        dlButtons[idx].ForeColor = ACCENT_GREEN;
                        dlButtons[idx].FlatAppearance.BorderColor = ACCENT_GREEN;
                        verifyButtons[idx].Enabled = false;
                        verifyButtons[idx].ForeColor = FG3;
                        verifyButtons[idx].FlatAppearance.BorderColor = FG3;
                        delButtons[idx].Enabled = false;
                        delButtons[idx].ForeColor = FG3;
                        delButtons[idx].FlatAppearance.BorderColor = FG3;
                        break;
                }
            };

            refresh();

            dlButtons[mi].Click += (s, e) => { ShowDownloader(idx); refresh(); };
            verifyButtons[mi].Click += (s, e) =>
            {
                string dir = Path.Combine(ModelsDir, MODELS[idx][0]);
                int ok = 0, bad = 0;
                List<int> badIndices = new List<int>();
                string details = "";

                var vs = new Form { Text = MODELS[idx][1], FormBorderStyle = FormBorderStyle.FixedSingle, StartPosition = FormStartPosition.CenterParent, Size = new Size(420, 120), BackColor = BG, TopMost = true, ShowInTaskbar = false, ControlBox = false };
                var vl = new Label { Text = "", Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(390, 22), Location = new Point(12, 12) };
                var vb = new ProgressBar { Location = new Point(12, 42), Size = new Size(390, 22), Style = ProgressBarStyle.Continuous, Maximum = FILES.Length };
                vs.Controls.Add(vl);
                vs.Controls.Add(vb);
                vs.Show();
                vs.Refresh();

                for (int fi = 0; fi < FILES.Length; fi++)
                {
                    vl.Text = FILES[fi];
                    vb.Value = fi + 1;
                    vl.Refresh();
                    vb.Refresh();
                    Application.DoEvents();

                    string fp = Path.Combine(dir, FILES[fi]);
                    if (File.Exists(fp))
                    {
                        long sz = new FileInfo(fp).Length;
                        if (sz == SIZES[idx][fi])
                        {
                            uint crc = ComputeCRC32(fp);
                            if (crc == CRC32S[idx][fi]) ok++;
                            else { bad++; badIndices.Add(fi); details += "\n  " + FILES[fi] + " \u2014 " + T("file_damaged"); }
                        }
                        else { bad++; badIndices.Add(fi); details += "\n  " + FILES[fi] + " \u2014 " + T("file_incomplete"); }
                    }
                    else { bad++; badIndices.Add(fi); details += "\n  " + FILES[fi] + " \u2014 " + T("file_not_found"); }
                }
                vs.Close();
                vs.Dispose();
                if (bad == 0)
                {
                    EnsureInstalledManifest(idx);
                    MessageBox.Show(T("all_ok") + "\n" + ok + "/" + FILES.Length, MODELS[idx][1], MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
                else
                {
                    var res = MessageBox.Show(
                        ok + " " + T("files_ok") + ", " + bad + " " + T("files_with_problems") + ":\n" + details + "\n\n" + T("redownload_question"),
                        MODELS[idx][1], MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                    if (res == DialogResult.Yes)
                    {
                        ShowDownloader(idx, badIndices.ToArray());
                    }
                }
                refresh();
            };
            delButtons[mi].Click += (s, e) =>
            {
                if (MessageBox.Show(T("confirm_delete") + "\n" + MODELS[idx][1], T("confirm_delete"), MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
                {
                    try { Directory.Delete(Path.Combine(ModelsDir, MODELS[idx][0]), true); Directory.CreateDirectory(Path.Combine(ModelsDir, MODELS[idx][0])); }
                    catch (Exception ex) { MessageBox.Show(ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error); }
                    refresh();
                }
            };
        }

        var btnClose = new Button { Text = T("exit"), Font = new Font("Segoe UI", 9), Size = new Size(80, 28), Location = new Point(200, form.Height - 75), FlatStyle = FlatStyle.Flat, BackColor = BG2, ForeColor = FG2 };
        btnClose.FlatAppearance.BorderColor = FG3;
        btnClose.Click += (s, e) => form.Close();
        form.Controls.Add(btnClose);
        form.ShowDialog();
    }

    // --- Downloader ---
    static void ShowDownloader(int mi, int[] onlyFiles = null)
    {
        string targetDir = Path.Combine(ModelsDir, MODELS[mi][0]);
        string modelUrl = MODELS[mi][2];
        string modelDisplay = MODELS[mi][1];
        bool cancelled = false;
        Exception downloadError = null;

        // Determine which files to download
        int[] dlFiles = onlyFiles ?? new int[FILES.Length];
        if (onlyFiles == null) { for (int i = 0; i < FILES.Length; i++) dlFiles[i] = i; }

        long totalBytes = 0;
        foreach (int fi in dlFiles) totalBytes += SIZES[mi][fi];

        var form = new Form
        {
            Text = T("downloading") + " " + modelDisplay,
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterParent,
            Size = new Size(460, 200),
            BackColor = BG,
            MaximizeBox = false,
            MinimizeBox = false,
            AutoScaleMode = AutoScaleMode.Font,
        };

        var statusLabel = new Label { Text = T("preparing"), Font = new Font("Segoe UI", 10), ForeColor = FG, AutoSize = false, Size = new Size(430, 22), Location = new Point(12, 15) };
        var fileLabel = new Label { Text = "", Font = new Font("Segoe UI", 8), ForeColor = FG2, AutoSize = false, Size = new Size(430, 18), Location = new Point(12, 42) };
        var progressBar = new ProgressBar { Minimum = 0, Maximum = 1000, Value = 0, Size = new Size(430, 24), Location = new Point(12, 68) };
        var speedLabel = new Label { Text = "", Font = new Font("Segoe UI", 9), ForeColor = FG2, AutoSize = false, Size = new Size(200, 20), Location = new Point(12, 98) };
        var etaLabel = new Label { Text = "", Font = new Font("Segoe UI", 9), ForeColor = ACCENT_PINK, AutoSize = false, Size = new Size(220, 20), Location = new Point(222, 98), TextAlign = ContentAlignment.TopRight };
        var btnCancel = new Button { Text = T("cancel"), Font = new Font("Segoe UI", 9), Size = new Size(90, 28), Location = new Point(180, 128), FlatStyle = FlatStyle.Flat, BackColor = BG3, ForeColor = FG };
        btnCancel.FlatAppearance.BorderColor = FG3;

        form.Controls.AddRange(new Control[] { statusLabel, fileLabel, progressBar, speedLabel, etaLabel, btnCancel });
        form.ControlBox = false;

        WebClient client = null;
        btnCancel.Click += (s, e) => { cancelled = true; if (client != null) client.CancelAsync(); };
        form.FormClosing += (s, e) => { cancelled = true; if (client != null) { try { client.CancelAsync(); } catch { } } };

        form.Shown += (s, e) =>
        {
            var bg = new BackgroundWorker();
            bg.DoWork += (sender, ev) =>
            {
                Directory.CreateDirectory(targetDir);
                client = new WebClient();
                long downloadedTotal = 0;
                DateTime startTime = DateTime.Now;

                // Count already-completed bytes (for partial re-download)
                foreach (int fi in dlFiles)
                {
                    string fp = Path.Combine(targetDir, FILES[fi]);
                    if (File.Exists(fp) && new FileInfo(fp).Length == SIZES[mi][fi])
                        downloadedTotal += SIZES[mi][fi];
                }

                int dlCount = dlFiles.Length;
                for (int di = 0; di < dlCount; di++)
                {
                    if (cancelled) break;
                    int fi = dlFiles[di];
                    string relPath = FILES[fi];
                    string destPath = Path.Combine(targetDir, relPath);

                    // Delete bad file before re-download
                    if (File.Exists(destPath))
                    {
                        if (new FileInfo(destPath).Length == SIZES[mi][fi] && ComputeCRC32(destPath) == CRC32S[mi][fi])
                            continue; // already good
                        try { File.Delete(destPath); } catch { }
                    }

                    string url = modelUrl + relPath.Replace("\\", "/");
                    string destDir = Path.GetDirectoryName(destPath);
                    if (!Directory.Exists(destDir)) Directory.CreateDirectory(destDir);

                    string fn = relPath;
                    int fIdx = fi;
                    form.Invoke((Action)(() =>
                    {
                        statusLabel.Text = T("downloading") + " " + (di + 1) + "/" + dlCount;
                        fileLabel.Text = T("file_" + fi);
                    }));

                    bool fileDone = false;
                    bool fileError = false;
                    Exception fileEx = null;

                    client.DownloadProgressChanged += (ss, pe) =>
                    {
                        if (cancelled) return;
                        long currentTotal = downloadedTotal + pe.BytesReceived;
                        int pct = (int)(currentTotal * 1000 / totalBytes);
                        if (pct > 1000) pct = 1000;
                        double elapsed = (DateTime.Now - startTime).TotalSeconds;
                        double speed = elapsed > 0 ? currentTotal / elapsed / 1024.0 / 1024.0 : 0;
                        long remaining = totalBytes - currentTotal;
                        double eta = speed > 0 ? remaining / (speed * 1024.0 * 1024.0) : 0;
                        string etaText = "";
                        if (eta > 3600) etaText = "~" + (int)(eta / 3600) + "h " + (int)((eta % 3600) / 60) + "m";
                        else if (eta > 60) etaText = "~" + (int)(eta / 60) + "m " + (int)(eta % 60) + "s";
                        else if (eta > 0) etaText = "~" + (int)eta + "s";
                        try { form.Invoke((Action)(() =>
                        {
                            progressBar.Value = pct;
                            speedLabel.Text = string.Format("{0:F1} MB/s  \u2014  {1} / {2}", speed, FormatSize(currentTotal), FormatSize(totalBytes));
                            etaLabel.Text = pct / 10 + "%  " + etaText;
                        })); }
                        catch { }
                    };

                    client.DownloadFileCompleted += (ss, pe) => { if (pe.Error != null) { fileError = true; fileEx = pe.Error; } fileDone = true; };
                    client.DownloadFileAsync(new Uri(url), destPath);
                    while (!fileDone && !cancelled) Application.DoEvents();
                    if (cancelled) break;
                    if (fileError) { downloadError = fileEx; break; }
                    downloadedTotal += SIZES[mi][fIdx];
                }
                client.Dispose();
                client = null;
            };
            bg.RunWorkerCompleted += (sender, ev) =>
            {
                form.Close();
                if (!cancelled && downloadError == null)
                    MessageBox.Show(T("all_ok") + "\n" + modelDisplay, T("downloading"), MessageBoxButtons.OK, MessageBoxIcon.Information);
            };
            bg.RunWorkerAsync();
        };

        form.ShowDialog();

        if (downloadError != null)
            MessageBox.Show(T("dl_failed") + "\n" + downloadError.Message, "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }
}

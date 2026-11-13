using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

// Portable launcher for PuriPuly-heart. Actions: run, run with debug,
// maintenance (user data cleanup, python runtime temp cleanup), exit.
// All paths are resolved relative to this exe; the folder name is not fixed.
// UI language follows Windows (35 languages embedded). Visual style follows
// the classic start.cs launcher. Source is UTF-8: compile with /codepage:65001.
static class Program
{
    const string LauncherVersion = "1.2.0";

    static string[] T;
    static Process startedProc;
    static bool startedDebug;
    static Color BG, BG2, BG3, FG, FG2, FG3;
    static Color ACCENT_PINK, ACCENT_GREEN, ACCENT_BLUE, ACCENT_YELLOW, ACCENT_RED;

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
        return true;
    }

    static void InitColors(bool dark)
    {
        if (dark)
        {
            BG = Color.FromArgb(28, 28, 32);
            BG2 = Color.FromArgb(36, 36, 40);
            BG3 = Color.FromArgb(44, 44, 48);
            FG = Color.FromArgb(230, 230, 230);
            FG2 = Color.FromArgb(170, 170, 178);
            FG3 = Color.FromArgb(160, 160, 170);
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
            FG2 = Color.FromArgb(70, 70, 78);
            FG3 = Color.FromArgb(100, 100, 110);
            ACCENT_PINK = Color.FromArgb(200, 60, 120);
            ACCENT_GREEN = Color.FromArgb(40, 160, 80);
            ACCENT_BLUE = Color.FromArgb(50, 100, 200);
            ACCENT_YELLOW = Color.FromArgb(180, 130, 20);
            ACCENT_RED = Color.FromArgb(200, 50, 50);
        }
    }

    // Translations: lang|run|run_debug|maint|exit|back|clean_data|clean_cache|
    // tip_run|tip_run_debug|tip_maint|tip_exit|tip_clean_data|tip_clean_cache|
    // tip_back|confirm_data|confirm_cache|confirm_title|done|done_errors|
    // err_not_found|err_launch
    const string RES = @"
ru|Запустить|Запустить с дебагом|Обслуживание|Выход|Назад|Очистить пользовательские данные|Очистить временные файлы исполнения Python|Запуск программы без дополнительных окон|Запуск с консолью рантайма Python и отладочными элементами интерфейса|Очистка данных и временных файлов без запуска программы|Закрыть лаунчер|Удалить содержимое data_user: настройки, модели, секреты, логи|Удалить __pycache__ (скомпилированный код) — используйте после изменения кода|Вернуться в главное меню|Удалить все пользовательские данные? Настройки, модели и секреты будут потеряны.|Удалить все временные файлы исполнения Python?|Подтверждение|Готово.|Готово. Ошибок:|Не найден:|Ошибка запуска
en|Launch|Launch with debug|Maintenance|Exit|Back|Clear user data|Clear Python runtime temporary files|Start the program without extra windows|Start with the Python runtime console and debug UI controls|Clear data and temporary files without starting the program|Close the launcher|Delete data_user contents: settings, models, secrets, logs|Delete __pycache__ (compiled code) — use after changing code|Return to the main menu|Delete all user data? Settings, models and secrets will be lost.|Delete all Python runtime temporary files?|Confirmation|Done.|Done. Errors:|Not found:|Launch error
de|Starten|Mit Debug starten|Wartung|Beenden|Zurück|Benutzerdaten löschen|Temporäre Python-Laufzeitdateien löschen|Programm ohne zusätzliche Fenster starten|Mit Python-Laufzeitkonsole und Debug-Oberflächenelementen starten|Daten und temporäre Dateien ohne Programmstart löschen|Launcher schließen|Inhalt von data_user löschen: Einstellungen, Modelle, Geheimnisse, Protokolle|__pycache__ (kompilierter Code) löschen — nach Codeänderungen verwenden|Zurück zum Hauptmenü|Alle Benutzerdaten löschen? Einstellungen, Modelle und Geheimnisse gehen verloren.|Alle temporären Python-Laufzeitdateien löschen?|Bestätigung|Fertig.|Fertig. Fehler:|Nicht gefunden:|Startfehler
fr|Lancer|Lancer avec débogage|Maintenance|Quitter|Retour|Effacer les données utilisateur|Effacer les fichiers temporaires d'exécution Python|Démarrer le programme sans fenêtres supplémentaires|Démarrer avec la console Python et les contrôles de débogage|Effacer les données et fichiers temporaires sans démarrer le programme|Fermer le lanceur|Supprimer le contenu de data_user : paramètres, modèles, secrets, journaux|Supprimer __pycache__ (code compilé) — à utiliser après modification du code|Retour au menu principal|Supprimer toutes les données utilisateur ? Paramètres, modèles et secrets seront perdus.|Supprimer tous les fichiers temporaires d'exécution Python ?|Confirmation|Terminé.|Terminé. Erreurs :|Introuvable :|Erreur de lancement
es|Iniciar|Iniciar con depuración|Mantenimiento|Salir|Volver|Borrar datos de usuario|Borrar archivos temporales de ejecución de Python|Iniciar el programa sin ventanas adicionales|Iniciar con la consola de Python y controles de depuración|Borrar datos y archivos temporales sin iniciar el programa|Cerrar el lanzador|Eliminar el contenido de data_user: ajustes, modelos, secretos, registros|Eliminar __pycache__ (código compilado) — usar tras cambiar el código|Volver al menú principal|¿Eliminar todos los datos de usuario? Se perderán ajustes, modelos y secretos.|¿Eliminar todos los archivos temporales de ejecución de Python?|Confirmación|Hecho.|Hecho. Errores:|No encontrado:|Error de inicio
it|Avvia|Avvia con debug|Manutenzione|Esci|Indietro|Elimina dati utente|Elimina file temporanei di esecuzione Python|Avvia il programma senza finestre aggiuntive|Avvia con la console Python e i controlli di debug|Elimina dati e file temporanei senza avviare il programma|Chiudi il launcher|Elimina il contenuto di data_user: impostazioni, modelli, segreti, log|Elimina __pycache__ (codice compilato) — usare dopo le modifiche al codice|Torna al menu principale|Eliminare tutti i dati utente? Impostazioni, modelli e segreti andranno persi.|Eliminare tutti i file temporanei di esecuzione Python?|Conferma|Fatto.|Fatto. Errori:|Non trovato:|Errore di avvio
pt|Iniciar|Iniciar com depuração|Manutenção|Sair|Voltar|Limpar dados do utilizador|Limpar ficheiros temporários de execução do Python|Iniciar o programa sem janelas adicionais|Iniciar com a consola do Python e controlos de depuração|Limpar dados e ficheiros temporários sem iniciar o programa|Fechar o lançador|Apagar o conteúdo de data_user: definições, modelos, segredos, registos|Apagar __pycache__ (código compilado) — usar após alterar o código|Voltar ao menu principal|Apagar todos os dados do utilizador? As definições, modelos e segredos serão perdidos.|Apagar todos os ficheiros temporários de execução do Python?|Confirmação|Concluído.|Concluído. Erros:|Não encontrado:|Erro ao iniciar
nl|Starten|Starten met foutopsporing|Onderhoud|Afsluiten|Terug|Gebruikersgegevens wissen|Tijdelijke Python-bestanden wissen|Programma starten zonder extra vensters|Starten met Python-console en foutopsporingsbediening|Gegevens en tijdelijke bestanden wissen zonder het programma te starten|Launcher sluiten|Inhoud van data_user verwijderen: instellingen, modellen, geheimen, logboeken|__pycache__ (gecompileerde code) verwijderen — gebruik na codewijzigingen|Terug naar het hoofdmenu|Alle gebruikersgegevens verwijderen? Instellingen, modellen en geheimen gaan verloren.|Alle tijdelijke Python-bestanden verwijderen?|Bevestiging|Voltooid.|Voltooid. Fouten:|Niet gevonden:|Startfout
pl|Uruchom|Uruchom z debugowaniem|Konserwacja|Zakończ|Wstecz|Wyczyść dane użytkownika|Wyczyść tymczasowe pliki środowiska Python|Uruchom program bez dodatkowych okien|Uruchom z konsolą Pythona i elementami debugowania|Wyczyść dane i pliki tymczasowe bez uruchamiania programu|Zamknij launcher|Usuń zawartość data_user: ustawienia, modele, sekrety, dzienniki|Usuń __pycache__ (skompilowany kod) — użyj po zmianie kodu|Wróć do menu głównego|Usunąć wszystkie dane użytkownika? Ustawienia, modele i sekrety zostaną utracone.|Usunąć wszystkie tymczasowe pliki środowiska Python?|Potwierdzenie|Gotowe.|Gotowe. Błędy:|Nie znaleziono:|Błąd uruchamiania
tr|Başlat|Hata ayıklama ile başlat|Bakım|Çıkış|Geri|Kullanıcı verilerini temizle|Python çalışma zamanı geçici dosyalarını temizle|Programı ek penceresiz başlat|Python çalışma zamanı konsolu ve hata ayıklama kontrolleriyle başlat|Programı başlatmadan verileri ve geçici dosyaları temizle|Başlatıcıyı kapat|data_user içeriğini sil: ayarlar, modeller, sırlar, günlükler|__pycache__ (derlenmiş kod) sil — kod değişikliklerinden sonra kullan|Ana menüye dön|Tüm kullanıcı verileri silinsin mi? Ayarlar, modeller ve sırlar kaybolacak.|Tüm Python çalışma zamanı geçici dosyaları silinsin mi?|Onay|Tamam.|Tamam. Hatalar:|Bulunamadı:|Başlatma hatası
ar|تشغيل|تشغيل مع التصحيح|الصيانة|خروج|رجوع|مسح بيانات المستخدم|مسح الملفات المؤقتة لتشغيل Python|تشغيل البرنامج بدون نوافذ إضافية|التشغيل مع وحدة تحكم Python وعناصر التصحيح|مسح البيانات والملفات المؤقتة بدون تشغيل البرنامج|إغلاق المشغّل|حذف محتويات data_user: الإعدادات والنماذج والأسرار والسجلات|حذف __pycache__ (الشيفرة المترجمة) — استخدمه بعد تعديل الشيفرة|العودة إلى القائمة الرئيسية|حذف جميع بيانات المستخدم؟ سيتم فقدان الإعدادات والنماذج والأسرار.|حذف جميع الملفات المؤقتة لتشغيل Python؟|تأكيد|تم.|تم. الأخطاء:|غير موجود:|خطأ في التشغيل
el|Εκκίνηση|Εκκίνηση με εντοπισμό σφαλμάτων|Συντήρηση|Έξοδος|Πίσω|Καθαρισμός δεδομένων χρήστη|Καθαρισμός προσωρινών αρχείων Python|Εκκίνηση του προγράμματος χωρίς επιπλέον παράθυρα|Εκκίνηση με κονσόλα Python και στοιχεία εντοπισμού σφαλμάτων|Καθαρισμός δεδομένων και προσωρινών αρχείων χωρίς εκκίνηση του προγράμματος|Κλείσιμο του εκκινητή|Διαγραφή περιεχομένων data_user: ρυθμίσεις, μοντέλα, μυστικά, αρχεία καταγραφής|Διαγραφή __pycache__ (μεταγλωττισμένος κώδικας) — χρησιμοποιήστε μετά αλλαγές κώδικα|Επιστροφή στο κύριο μενού|Διαγραφή όλων των δεδομένων χρήστη; Οι ρυθμίσεις, τα μοντέλα και τα μυστικά θα χαθούν.|Διαγραφή όλων των προσωρινών αρχείων Python;|Επιβεβαίωση|Έτοιμο.|Έτοιμο. Σφάλματα:|Δεν βρέθηκε:|Σφάλμα εκκίνησης
bg|Стартиране|Стартиране с дебъг|Поддръжка|Изход|Назад|Изчистване на потребителските данни|Изчистване на временните файлове на Python|Стартиране на програмата без допълнителни прозорци|Стартиране с конзола на Python и елементи за дебъг|Изчистване на данни и временни файлове без стартиране на програмата|Затваряне на стартера|Изтриване на съдържанието на data_user: настройки, модели, тайни, логове|Изтриване на __pycache__ (компилиран код) — използвайте след промени на кода|Обратно към главното меню|Изтриване на всички потребителски данни? Настройките, моделите и тайните ще бъдат загубени.|Изтриване на всички временни файлове на Python?|Потвърждение|Готово.|Готово. Грешки:|Не е намерено:|Грешка при стартиране
uk|Запустити|Запустити з налагодженням|Обслуговування|Вихід|Назад|Очистити дані користувача|Очистити тимчасові файли виконання Python|Запуск програми без додаткових вікон|Запуск з консоллю Python та елементами налагодження|Очищення даних і тимчасових файлів без запуску програми|Закрити лаунчер|Видалити вміст data_user: налаштування, моделі, секрети, журнали|Видалити __pycache__ (скомпільований код) — використовуйте після зміни коду|Повернутися до головного меню|Видалити всі дані користувача? Налаштування, моделі та секрети буде втрачено.|Видалити всі тимчасові файли виконання Python?|Підтвердження|Готово.|Готово. Помилок:|Не знайдено:|Помилка запуску
cs|Spustit|Spustit s laděním|Údržba|Konec|Zpět|Smazat uživatelská data|Smazat dočasné soubory běhu Pythonu|Spustit program bez dalších oken|Spustit s konzolí Pythonu a ladicími prvky|Smazat data a dočasné soubory bez spuštění programu|Zavřít spouštěč|Smazat obsah data_user: nastavení, modely, tajemství, protokoly|Smazat __pycache__ (zkompilovaný kód) — použijte po změně kódu|Zpět do hlavního menu|Smazat všechna uživatelská data? Nastavení, modely a tajemství budou ztraceny.|Smazat všechny dočasné soubory běhu Pythonu?|Potvrzení|Hotovo.|Hotovo. Chyby:|Nenalezeno:|Chyba spuštění
sk|Spustiť|Spustiť s ladením|Údržba|Koniec|Späť|Odstrániť používateľské údaje|Odstrániť dočasné súbory behu Pythonu|Spustiť program bez ďalších okien|Spustiť s konzolou Pythonu a ladiacimi prvkami|Odstrániť údaje a dočasné súbory bez spustenia programu|Zavrieť spúšťač|Odstrániť obsah data_user: nastavenia, modely, tajomstvá, protokoly|Odstrániť __pycache__ (skompilovaný kód) — použite po zmene kódu|Späť do hlavného menu|Odstrániť všetky používateľské údaje? Nastavenia, modely a tajomstvá budú stratené.|Odstrániť všetky dočasné súbory behu Pythonu?|Potvrdenie|Hotovo.|Hotovo. Chyby:|Nenájdené:|Chyba spustenia
hu|Indítás|Indítás hibakereséssel|Karbantartás|Kilépés|Vissza|Felhasználói adatok törlése|Python futtatórendszer ideiglenes fájljainak törlése|A program indítása további ablakok nélkül|Indítás a Python konzollal és hibakeresési vezérlőkkel|Adatok és ideiglenes fájlok törlése a program indítása nélkül|Az indító bezárása|A data_user tartalmának törlése: beállítások, modellek, titkok, naplók|A __pycache__ (fordított kód) törlése — kódmódosítás után használja|Vissza a főmenübe|Törli az összes felhasználói adatot? A beállítások, modellek és titkok elvesznek.|Törli az összes Python ideiglenes fájlt?|Megerősítés|Kész.|Kész. Hibák:|Nem található:|Indítási hiba
ro|Lansare|Lansare cu depanare|Întreținere|Ieșire|Înapoi|Ștergere date utilizator|Ștergere fișiere temporare de execuție Python|Pornește programul fără ferestre suplimentare|Pornește cu consola Python și controale de depanare|Șterge datele și fișierele temporare fără a porni programul|Închide lansatorul|Șterge conținutul data_user: setări, modele, secrete, jurnale|Șterge __pycache__ (cod compilat) — folosiți după modificarea codului|Înapoi la meniul principal|Ștergi toate datele utilizator? Setările, modelele și secretele vor fi pierdute.|Ștergi toate fișierele temporare de execuție Python?|Confirmare|Gata.|Gata. Erori:|Negăsit:|Eroare de lansare
da|Start|Start med fejlfinding|Vedligeholdelse|Afslut|Tilbage|Ryd brugerdata|Ryd midlertidige Python-filer|Start programmet uden ekstra vinduer|Start med Python-konsollen og fejlfindingskontroller|Ryd data og midlertidige filer uden at starte programmet|Luk launcher|Slet indholdet af data_user: indstillinger, modeller, hemmeligheder, logfiler|Slet __pycache__ (kompileret kode) — brug efter ændringer i koden|Tilbage til hovedmenuen|Slet alle brugerdata? Indstillinger, modeller og hemmeligheder vil gå tabt.|Slet alle midlertidige Python-filer?|Bekræftelse|Færdig.|Færdig. Fejl:|Ikke fundet:|Startfeil
no|Start|Start med feilsøking|Vedlikehold|Avslutt|Tilbake|Tøm brukerdata|Tøm midlertidige Python-filer|Start programmet uten ekstra vinduer|Start med Python-konsollen og feilsøkingskontroller|Tøm data og midlertidige filer uten å starte programmet|Lukk lanseringen|Slett innholdet i data_user: innstillinger, modeller, hemmeligheter, logger|Slett __pycache__ (kompilert kode) — bruk etter endringer i koden|Tilbake til hovedmenyen|Slette alle brukerdata? Innstillinger, modeller og hemmeligheter vil gå tapt.|Slette alle midlertidige Python-filer?|Bekreftelse|Ferdig.|Ferdig. Feil:|Ikke funnet:|Startfeil
sv|Starta|Starta med felsökning|Underhåll|Avsluta|Tillbaka|Rensa användardata|Rensa temporära Python-filer|Starta programmet utan extra fönster|Starta med Python-konsolen och felsökningskontroller|Rensa data och temporära filer utan att starta programmet|Stäng startprogrammet|Radera innehållet i data_user: inställningar, modeller, hemligheter, loggar|Radera __pycache__ (kompilerad kod) — använd efter kodändringar|Tillbaka till huvudmenyn|Radera all användardata? Inställningar, modeller och hemligheter går förlorade.|Radera alla temporära Python-filer?|Bekräftelse|Klart.|Klart. Fel:|Hittades inte:|Startfel
fi|Käynnistä|Käynnistä vianhakutilassa|Ylläpito|Poistu|Takaisin|Tyhjennä käyttäjätiedot|Tyhjennä Pythonin väliaikaiset tiedostot|Käynnistä ilman lisää ikkunoita|Käynnistä Python-konsolin ja vianhallintasäätimien kanssa|Tyhjennä tiedot ja väliaikaiset tiedostot käynnistämättä ohjelmaa|Sulje käynnistin|Poista data_userin sisältö: asetukset, mallit, salaiset tiedot, lokit|Poista __pycache__ (käännetty koodi) — käytä koodin muutosten jälkeen|Takaisin päävalikkoon|Poistetaanko kaikki käyttäjätiedot? Asetukset, mallit ja salaiset tiedot katoavat.|Poistetaanko kaikki Pythonin väliaikaiset tiedostot?|Vahvistus|Valmis.|Valmis. Virheet:|Ei löytynyt:|Käynnistysvirhe
et|Käivita|Käivita silumisega|Hooldus|Välju|Tagasi|Kustuta kasutajaandmed|Kustuta Pythoni ajutised failid|Käivita programm ilma lisakentadeta|Käivita Pythoni konsooli ja silumisjuhtelementidega|Kustuta andmed ja ajutised failid ilma programmi käivitamata|Sulge käiviti|Kustuta data_user sisu: sätted, mudelid, saladused, logid|Kustuta __pycache__ (kompileeritud kood) — kasuta pärast koodi muutmist|Tagasi põhimenüüsse|Kustutada kõik kasutajaandmed? Sätted, mudelid ja saladused lähevad kaotsi.|Kustutada kõik Pythoni ajutised failid?|Kinnitus|Valmis.|Valmis. Vead:|Ei leitud:|Käivitusviga
lv|Palaist|Palaist ar atkļūdošanu|Apkope|Iziet|Atpakaļ|Notīrīt lietotāja datus|Notīrīt Python pagaidu failus|Palaist programmu bez papildu logiem|Palaist ar Python konsoli un atkļūdošanas vadīklām|Notīrīt datus un pagaidu failus, nepalaižot programmu|Aizvērt palaidēju|Dzēst data_user saturu: iestatījumus, modeļus, noslēpumus, žurnālus|Dzēst __pycache__ (kompilēto kodu) — lietot pēc kodu izmaiņām|Atpakaļ uz galveno izvēlni|Dzēst visus lietotāja datus? Iestatījumi, modeļi un noslēpumi tiks zaudēti.|Dzēst visus Python pagaidu failus?|Apstiprinājums|Gatavs.|Gatavs. Kļūdas:|Nav atrasts:|Palaišanas kļūda
lt|Paleisti|Paleisti su derinimu|Priežiūra|Išeiti|Atgal|Išvalyti naudotojo duomenis|Išvalyti Python laikinus failus|Paleisti programą be papildomų langų|Paleisti su Python konsole ir derinimo valdikliais|Išvalyti duomenis ir laikinus failus nepaleidžiant programos|Uždaryti paleistuvę|Ištrinti data_user turinį: nustatymus, modelius, paslaptis, žurnalus|Ištrinti __pycache__ (kompiliuotą kodą) — naudoti pakeitus kodą|Atgal į pagrindinį meniu|Ištrinti visus naudotojo duomenis? Nustatymai, modeliai ir paslaptys bus prarasti.|Ištrinti visus Python laikinus failus?|Patvirtinimas|Baigta.|Baigta. Klaidos:|Nerasta:|Paleidimo klaida
hi|चलाएँ|डिबग के साथ चलाएँ|रखरखाव|बाहर निकलें|वापस|उपयोगकर्ता डेटा साफ़ करें|Python रनटाइम अस्थायी फ़ाइलें साफ़ करें|बिना अतिरिक्त विंडो के प्रोग्राम चलाएँ|Python कंसोल और डिबग नियंत्रणों के साथ चलाएँ|प्रोग्राम चलाए बिना डेटा और अस्थायी फ़ाइलें साफ़ करें|लॉन्चर बंद करें|data_user की सामग्री हटाएँ: सेटिंग्स, मॉडल, रहस्य, लॉग|__pycache__ (संकलित कोड) हटाएँ — कोड बदलने के बाद उपयोग करें|मुख्य मेनू पर लौटें|सभी उपयोगकर्ता डेटा हटाएँ? सेटिंग्स, मॉडल और रहस्य नष्ट हो जाएँगे।|सभी Python अस्थायी फ़ाइलें हटाएँ?|पुष्टि|हो गया।|हो गया। त्रुटियाँ:|नहीं मिला:|चलाने में त्रुटि
th|เริ่มทำงาน|เริ่มพร้อมดีบัก|บำรุงรักษา|ออก|ย้อนกลับ|ล้างข้อมูลผู้ใช้|ล้างไฟล์ชั่วคราวของ Python|เริ่มโปรแกรมโดยไม่มีหน้าต่างเพิ่มเติม|เริ่มพร้อมคอนโซล Python และตัวควบคุมดีบัก|ล้างข้อมูลและไฟล์ชั่วคราวโดยไม่เริ่มโปรแกรม|ปิดตัวเรียกใช้|ลบเนื้อหาของ data_user: การตั้งค่า โมเดล ความลับ บันทึก|ลบ __pycache__ (โค้ดที่คอมไพล์แล้ว) — ใช้หลังแก้ไขโค้ด|กลับไปเมนูหลัก|ลบข้อมูลผู้ใช้ทั้งหมดหรือไม่? การตั้งค่า โมเดล และความลับจะหายไป|ลบไฟล์ชั่วคราวของ Python ทั้งหมดหรือไม่?|ยืนยัน|เสร็จสิ้น|เสร็จสิ้น ข้อผิดพลาด:|ไม่พบ:|ข้อผิดพลาดในการเริ่ม
vi|Khởi chạy|Khởi chạy với gỡ lỗi|Bảo trì|Thoát|Quay lại|Xóa dữ liệu người dùng|Xóa tệp tạm của Python|Khởi động chương trình mà không có cửa sổ phụ|Khởi động với bảng điều khiển Python và điều khiển gỡ lỗi|Xóa dữ liệu và tệp tạm mà không khởi động chương trình|Đóng trình khởi chạy|Xóa nội dung data_user: cài đặt, mô hình, bí mật, nhật ký|Xóa __pycache__ (mã đã biên dịch) — dùng sau khi sửa mã|Quay lại menu chính|Xóa toàn bộ dữ liệu người dùng? Cài đặt, mô hình và bí mật sẽ bị mất.|Xóa toàn bộ tệp tạm của Python?|Xác nhận|Xong.|Xong. Lỗi:|Không tìm thấy:|Lỗi khởi chạy
id|Jalankan|Jalankan dengan debug|Pemeliharaan|Keluar|Kembali|Hapus data pengguna|Hapus file sementara runtime Python|Jalankan program tanpa jendela tambahan|Jalankan dengan konsol Python dan kontrol debug|Hapus data dan file sementara tanpa menjalankan program|Tutup peluncur|Hapus isi data_user: pengaturan, model, rahasia, log|Hapus __pycache__ (kode terkompilasi) — gunakan setelah mengubah kode|Kembali ke menu utama|Hapus semua data pengguna? Pengaturan, model, dan rahasia akan hilang.|Hapus semua file sementara Python?|Konfirmasi|Selesai.|Selesai. Kesalahan:|Tidak ditemukan:|Kesalahan menjalankan
ms|Lancar|Lancar dengan nyahpepijat|Penyelenggaraan|Keluar|Kembali|Kosongkan data pengguna|Kosongkan fail sementara Python|Lancar program tanpa tetingkap tambahan|Lancar dengan konsol Python dan kawalan nyahpepijat|Kosongkan data dan fail sementara tanpa menjalankan program|Tutup pelancar|Padam kandungan data_user: tetapan, model, rahsia, log|Padam __pycache__ (kod dikompilasi) — guna selepas menukar kod|Kembali ke menu utama|Padam semua data pengguna? Tetapan, model dan rahsia akan hilang.|Padam semua fail sementara Python?|Pengesahan|Selesai.|Selesai. Ralat:|Tidak dijumpai:|Ralat pelancaran
ca|Inicia|Inicia amb depuració|Manteniment|Surt|Enrere|Esborra les dades d'usuari|Esborra els fitxers temporals d'execució de Python|Inicia el programa sense finestres addicionals|Inicia amb la consola de Python i els controls de depuració|Esborra dades i fitxers temporals sense iniciar el programa|Tanca el llançador|Suprimeix el contingut de data_user: configuració, models, secrets, registres|Suprimeix __pycache__ (codi compilat) — fes-lo servir després de canviar el codi|Torna al menú principal|Suprimir totes les dades d'usuari? Es perdran la configuració, els models i els secrets.|Suprimir tots els fitxers temporals de Python?|Confirmació|Fet.|Fet. Errors:|No s'ha trobat:|Error d'inici
zh-CN|启动|调试模式启动|维护|退出|返回|清除用户数据|清除 Python 运行时临时文件|启动程序，不显示额外窗口|带 Python 控制台和调试控件启动|不启动程序，清理数据和临时文件|关闭启动器|删除 data_user 内容：设置、模型、密钥、日志|删除 __pycache__（已编译代码）——修改代码后使用|返回主菜单|删除所有用户数据？设置、模型和密钥将丢失。|删除所有 Python 运行时临时文件？|确认|完成。|完成。错误：|未找到：|启动错误
zh-TW|啟動|偵錯模式啟動|維護|結束|返回|清除使用者資料|清除 Python 執行時期暫存檔|啟動程式，不顯示額外視窗|帶 Python 主控台和偵錯控制項啟動|不啟動程式，清除資料與暫存檔|關閉啟動器|刪除 data_user 內容：設定、模型、密鑰、記錄|刪除 __pycache__（已編譯程式碼）——修改程式碼後使用|返回主選單|刪除所有使用者資料？設定、模型和密鑰將遺失。|刪除所有 Python 執行時期暫存檔？|確認|完成。|完成。錯誤：|找不到：|啟動錯誤
ja|起動|デバッグで起動|メンテナンス|終了|戻る|ユーザーデータを削除|Python 実行時一時ファイルを削除|余分なウィンドウなしでプログラムを起動|Python コンソールとデバッグ操作を表示して起動|プログラムを起動せずにデータと一時ファイルを削除|ランチャーを閉じる|data_user の内容を削除: 設定、モデル、シークレット、ログ|__pycache__ (コンパイル済みコード) を削除 — コード変更後に使用|メインメニューに戻る|すべてのユーザーデータを削除しますか? 設定、モデル、シークレットは失われます。|すべての Python 一時ファイルを削除しますか?|確認|完了。|完了。エラー:|見つかりません:|起動エラー
ko|실행|디버그로 실행|유지 관리|종료|뒤로|사용자 데이터 삭제|Python 실행 임시 파일 삭제|추가 창 없이 프로그램 실행|Python 콘솔과 디버그 컨트롤과 함께 실행|프로그램 실행 없이 데이터와 임시 파일 삭제|실행기 닫기|data_user 내용 삭제: 설정, 모델, 비밀, 로그|__pycache__(컴파일된 코드) 삭제 — 코드 변경 후 사용|기본 메뉴로 돌아가기|모든 사용자 데이터를 삭제할까요? 설정, 모델, 비밀이 사라집니다.|모든 Python 임시 파일을 삭제할까요?|확인|완료.|완료. 오류:|찾을 수 없음:|실행 오류
";

    // Cache cleanup: lang|clean_cache_folder|tip_cache_folder|confirm_cache_folder
    const string RES2 = @"
ru|Очистить кэш|Удалить содержимое папки cache: скачанные пакеты, кэши клиентов, временные файлы|Удалить всё содержимое папки cache? Это пересоздаваемые кэши, они восстановятся автоматически.|Программа завершилась с ошибкой (код {0}). Для диагностики запустите с дебагом.|Некоторые файлы заняты — закройте программу и повторите.
en|Clear cache|Delete the contents of the cache folder: downloaded packages, client caches, temp files|Delete all contents of the cache folder? These are regenerable caches and will be recreated automatically.|The program exited with an error (code {0}). Launch with debug for diagnostics.|Some files are in use — close the program and retry.
de|Cache leeren|Inhalt des Ordners cache löschen: heruntergeladene Pakete, Client-Caches, temporäre Dateien|Den gesamten Inhalt des Ordners cache löschen? Es sind regenerierbare Caches und werden automatisch neu erstellt.|Das Programm wurde mit einem Fehler beendet (Code {0}). Zur Diagnose mit Debug starten.|Einige Dateien sind belegt — Programm schließen und erneut versuchen.
fr|Vider le cache|Supprimer le contenu du dossier cache : paquets téléchargés, caches clients, fichiers temporaires|Supprimer tout le contenu du dossier cache ? Ce sont des caches régénérables, recréés automatiquement.|Le programme s'est terminé avec une erreur (code {0}). Lancez avec débogage pour diagnostiquer.|Certains fichiers sont utilisés — fermez le programme et réessayez.
es|Vaciar caché|Eliminar el contenido de la carpeta cache: paquetes descargados, cachés de clientes, archivos temporales|¿Eliminar todo el contenido de la carpeta cache? Son cachés regenerables y se recrearán automáticamente.|El programa terminó con un error (código {0}). Inicie con depuración para diagnosticar.|Algunos archivos están en uso — cierre el programa e inténtelo de nuevo.
it|Svuota la cache|Elimina il contenuto della cartella cache: pacchetti scaricati, cache dei client, file temporanei|Eliminare tutto il contenuto della cartella cache? Sono cache rigenerabili e verranno ricreate automaticamente.|Il programma è terminato con un errore (codice {0}). Avvia con debug per diagnosticare.|Alcuni file sono in uso — chiudi il programma e riprova.
pt|Limpar cache|Apagar o conteúdo da pasta cache: pacotes descarregados, caches de clientes, ficheiros temporários|Apagar todo o conteúdo da pasta cache? São caches regeneráveis e serão recriados automaticamente.|O programa terminou com um erro (código {0}). Inicie com depuração para diagnosticar.|Alguns ficheiros estão em uso — feche o programa e tente novamente.
nl|Cache wissen|Inhoud van de map cache verwijderen: gedownloade pakketten, client-caches, tijdelijke bestanden|Alle inhoud van de map cache verwijderen? Dit zijn regenereerbare caches en worden automatisch opnieuw aangemaakt.|Het programma is beëindigd met een fout (code {0}). Start met foutopsporing voor diagnose.|Sommige bestanden zijn in gebruik — sluit het programma en probeer opnieuw.
pl|Wyczyść pamięć podręczną|Usuń zawartość folderu cache: pobrane pakiety, pamięci podręczne klientów, pliki tymczasowe|Usunąć całą zawartość folderu cache? To regenerowalne pamięci podręczne, odtworzą się automatycznie.|Program zakończył się błędem (kod {0}). Uruchom z debugowaniem, aby zdiagnozować.|Niektóre pliki są używane — zamknij program i spróbuj ponownie.
tr|Önbelleği temizle|cache klasörünün içeriğini sil: indirilen paketler, istemci önbellekleri, geçici dosyalar|cache klasörünün tüm içeriği silinsin mi? Bunlar yeniden oluşturulabilir önbelleklerdir, otomatik olarak geri gelir.|Program bir hatayla sonlandı (kod {0}). Tanı için hata ayıklama ile başlatın.|Bazı dosyalar kullanımda — programı kapatıp tekrar deneyin.
ar|مسح الذاكرة المؤقتة|حذف محتوى مجلد cache: الحزم المُنزَّلة وذاكرة العملاء المؤقتة والملفات المؤقتة|حذف كل محتوى مجلد cache؟ هذه ذاكرات مؤقتة قابلة لإعادة الإنشاء وستُعاد تلقائيًا.|انتهى البرنامج بخطأ (الرمز {0}). شغّل مع التصحيح للتشخيص.|بعض الملفات قيد الاستخدام — أغلق البرنامج وأعد المحاولة.
el|Καθαρισμός προσωρινής μνήμης|Διαγραφή περιεχομένων του φακέλου cache: ληφθέντα πακέτα, προσωρινές μνήμες πελατών, προσωρινά αρχεία|Διαγραφή όλων των περιεχομένων του φακέλου cache; Είναι αναγεννήσιμες προσωρινές μνήμες και θα δημιουργηθούν ξανά αυτόματα.|Το πρόγραμμα τερμάτισε με σφάλμα (κωδικός {0}). Εκκινήστε με εντοπισμό σφαλμάτων για διάγνωση|Ορισμένα αρχεία χρησιμοποιούνται — κλείστε το πρόγραμμα και δοκιμάστε ξανά.
bg|Изчистване на кеша|Изтриване на съдържанието на папка cache: изтеглени пакети, кешове на клиенти, временни файлове|Изтриване на цялото съдържание на папка cache? Това са регенерируеми кешове и ще бъдат създадени отново автоматично.|Програмата завърши с грешка (код {0}). Стартирайте с дебъг за диагностика.|Някои файлове са заети — затворете програмата и опитайте отново.
uk|Очистити кеш|Видалити вміст папки cache: завантажені пакети, кеші клієнтів, тимчасові файли|Видалити весь вміст папки cache? Це відновлювані кеші, вони відтворяться автоматично.|Програма завершилася з помилкою (код {0}). Для діагностики запустіть із налагодженням.|Деякі файли зайняті — закрийте програму та повторіть.
cs|Vymazat mezipaměť|Smazat obsah složky cache: stažené balíčky, mezipaměti klientů, dočasné soubory|Smazat celý obsah složky cache? Jde o regenerovatelné mezipaměti, obnoví se automaticky.|Program skončil s chybou (kód {0}). Spusťte s laděním pro diagnostiku.|Některé soubory jsou používány — zavřete program a zkuste to znovu.
sk|Vymazať vyrovnávaciu pamäť|Odstrániť obsah priečinka cache: stiahnuté balíčky, vyrovnávacie pamäte klientov, dočasné súbory|Odstrániť celý obsah priečinka cache? Ide o regenerovateľné vyrovnávacie pamäte, obnovia sa automaticky.|Program sa skončil s chybou (kód {0}). Spustite s ladením na diagnostiku.|Niektoré súbory sú používané — zavrite program a skúste to znova.
hu|Gyorsítótár törlése|A cache mappa tartalmának törlése: letöltött csomagok, kliens gyorsítótárak, ideiglenes fájlok|A cache mappa teljes tartalma törölhető? Ezek újragenerálható gyorsítótárak, automatikusan újra létrejönnek.|A program hibával fejeződött be ({0} kód). Diagnosztikához indítsa hibakereséssel.|Egyes fájlok használatban vannak — zárja be a programot, és próbálja újra.
ro|Golire cache|Șterge conținutul folderului cache: pachete descărcate, cache-uri de clienți, fișiere temporare|Ștergi tot conținutul folderului cache? Sunt cache-uri regenerabile și vor fi recreate automat.|Programul s-a încheiat cu o eroare (cod {0}). Lansați cu depanare pentru diagnostic.|Unele fișiere sunt utilizate — închideți programul și încercați din nou.
da|Ryd cachen|Slet indholdet af mappen cache: downloadede pakker, klientcaches, midlertidige filer|Slet alt indhold i mappen cache? Det er regenererbare caches og genskabes automatisk.|Programmet afsluttede med en fejl (kode {0}). Start med fejlfinding for at diagnosticere.|Nogle filer er i brug — luk programmet og prøv igen.
no|Tøm hurtigbufferen|Slett innholdet i mappen cache: nedlastede pakker, klientbuffere, midlertidige filer|Slette alt innhold i mappen cache? Dette er regenererbare buffere og opprettes automatisk på nytt.|Programmet ble avsluttet med en feil (kode {0}). Start med feilsøking for diagnostikk.|Noen filer er i bruk — lukk programmet og prøv igjen.
sv|Rensa cachen|Radera innehållet i mappen cache: nedladdade paket, klientcache, temporära filer|Radera allt innehåll i mappen cache? Det är regenererbara caches och återskapas automatiskt.|Programmet avslutades med ett fel (kod {0}). Starta med felsökning för diagnostik.|Vissa filer används — stäng programmet och försök igen.
fi|Tyhjennä välimuisti|Poista cache-kansion sisältö: ladatut paketit, asiakasvälimuistit, väliaikaiset tiedostot|Poistetaanko koko cache-kansion sisältö? Nämä ovat uudelleen luotavia välimuisteja ja luodaan automaattisesti uudelleen.|Päättyi virheeseen (koodi {0}). Käynnistä vianhakutilassa diagnostiikkaa varten.|Jotkin tiedostot ovat käytössä — sulje ohjelma ja yritä uudelleen.
et|Tühjenda vahemälu|Kustuta kausta cache sisu: allalaaditud paketid, klientide vahemälud, ajutised failid|Kustutada kogu kausta cache sisu? Need on taastootavad vahemälud ja luuakse automaatselt uuesti.|Program lõppes veaga (koodi {0}). Diagnoosimiseks käivita silumisega.|Mõned failid on kasutuses — sulge programm ja proovi uuesti.
lv|Notīrīt kešatmiņu|Dzēst mapes cache saturu: lejupielādētās paketes, klientu kešatmiņas, pagaidu failus|Dzēst visu mapes cache saturu? Tie ir atjaunojami kešatmiņas faili, kas tiks atjaunoti automātiski.|Programma beidzās ar kļūdu (kods {0}). Diagnostikai palaidiet ar atkļūdošanu.|Daži faili ir aizņemti — aizveriet programmu un mēģiniet vēlreiz.
lt|Išvalyti talpyklą|Ištrinti aplanko cache turinį: atsisiųstus paketus, klientų talpyklas, laikinus failus|Ištrinti visą aplanko cache turinį? Tai atkuriamos talpyklos, bus sukurtos iš naujo automatiškai.|Programa baigėsi klaida (kodas {0}). Diagnostikai paleiskite su derinimu.|Kai kurie failai naudojami — uždarykite programą ir bandykite dar kartą.
hi|कैश साफ़ करें|cache फ़ोल्डर की सामग्री हटाएँ: डाउनलोड किए गए पैकेज, क्लाइंट कैश, अस्थायी फ़ाइलें|cache फ़ोल्डर की सभी सामग्री हटाएँ? ये पुनर्निर्मित होने वाले कैश हैं, स्वतः फिर से बन जाएँगे।|प्रोग्राम त्रुटि के साथ समाप्त हुआ (कोड {0})। निदान के लिए डिबग के साथ चलाएँ।|कुछ फ़ाइलें उपयोग में हैं — प्रोग्राम बंद करके पुनः प्रयास करें।
th|ล้างแคช|ลบเนื้อหาของโฟลเดอร์ cache: แพ็กเกจที่ดาวน์โหลด แคชของไคลเอนต์ ไฟล์ชั่วคราว|ลบเนื้อหาทั้งหมดของโฟลเดอร์ cache หรือไม่? เหล่านี้เป็นแคชที่สร้างใหม่ได้และจะถูกสร้างขึ้นใหม่โดยอัตโนมัติ|โปรแกรมสิ้นสุดด้วยข้อผิดพลาด (รหัส {0}) เริ่มพร้อมดีบักเพื่อวินิจฉัย|ไฟล์บางไฟล์กำลังถูกใช้งาน — ปิดโปรแกรมแล้วลองอีกครั้ง
vi|Xóa bộ nhớ đệm|Xóa nội dung thư mục cache: gói đã tải, bộ nhớ đệm của khách, tệp tạm|Xóa toàn bộ nội dung thư mục cache? Đây là bộ nhớ đệm có thể tạo lại và sẽ tự động được tạo mới.|Chương trình kết thúc với lỗi (mã {0}). Khởi chạy với gỡ lỗi để chẩn đoán.|Một số tệp đang được sử dụng — đóng chương trình và thử lại.
id|Bersihkan cache|Hapus isi folder cache: paket yang diunduh, cache klien, file sementara|Hapus semua isi folder cache? Ini adalah cache yang dapat dibuat ulang dan akan dibuat ulang secara otomatis.|Program berakhir dengan error (kode {0}). Jalankan dengan debug untuk diagnosis.|Beberapa file sedang digunakan — tutup program dan coba lagi.
ms|Kosongkan cache|Padam kandungan folder cache: pakej yang dimuat turun, cache klien, fail sementara|Padam semua kandungan folder cache? Ini ialah cache yang boleh dijana semula dan akan diwujudkan semula secara automatik.|Program tamat dengan ralat (kod {0}). Lancar dengan nyahpepijat untuk diagnosis.|Sesetengah fail sedang digunakan — tutup program dan cuba lagi.
ca|Buida la memòria cau|Esborra el contingut de la carpeta cache: paquets baixats, memòries cau de clients, fitxers temporals|Esborrar tot el contingut de la carpeta cache? Són memòries cau regenerables i es recrearan automàticament.|El programa ha acabat amb un error (codi {0}). Inicia amb depuració per diagnosticar.|Alguns fitxers estan en ús — tanqueu el programa i torneu-ho a provar.
zh-CN|清除缓存|删除 cache 文件夹的内容：已下载的包、客户端缓存、临时文件|删除 cache 文件夹的全部内容？这些是可重新生成的缓存，会自动重建。|程序出错退出（代码 {0}）。请用调试模式启动以诊断。|部分文件被占用 — 请关闭程序后重试。
zh-TW|清除快取|刪除 cache 資料夾的內容：已下載的套件、用戶端快取、暫存檔|刪除 cache 資料夾的全部內容？這些是可重新產生的快取，會自動重建。|程式錯誤結束（代碼 {0}）。請用偵錯模式啟動以診斷。|部分檔案被佔用 — 請關閉程式後重試。
ja|キャッシュを消去|cache フォルダーの内容を削除: ダウンロードしたパッケージ、クライアントキャッシュ、一時ファイル|cache フォルダーの内容をすべて削除しますか? これらは再生成可能なキャッシュで、自動的に再作成されます。|プログラムはエラーで終了しました (コード {0})。診断にはデバッグで起動してください。|一部のファイルが使用中です — プログラムを閉じて再試行してください。
ko|캐시 지우기|cache 폴더의 내용 삭제: 다운로드한 패키지, 클라이언트 캐시, 임시 파일|cache 폴더의 전체 내용을 삭제할까요? 재생성 가능한 캐시이며 자동으로 다시 생성됩니다.|프로그램이 오류로 종료되었습니다 (코드 {0}). 진단하려면 디버그로 실행하세요.|일부 파일이 사용 중입니다 — 프로그램을 닫고 다시 시도하세요.
";

    static string[] Tr()
    {
        Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
        List<string> order = new List<string>();
        foreach (string row in RES.Split('\n'))
        {
            string line = row.Trim();
            if (line.Length == 0) continue;
            string[] parts = line.Split('|');
            if (parts.Length >= 22 && !map.ContainsKey(parts[0]))
            {
                map[parts[0]] = parts;
                order.Add(parts[0]);
            }
        }
        foreach (string row in RES2.Split('\n'))
        {
            string line = row.Trim();
            if (line.Length == 0) continue;
            string[] parts = line.Split('|');
            if (parts.Length >= 4 && map.ContainsKey(parts[0]))
            {
                string[] merged = new string[27];
                map[parts[0]].CopyTo(merged, 0);
                for (int i = 1; i < parts.Length && i < 6; i++) merged[21 + i] = parts[i];
                map[parts[0]] = merged;
            }
        }
        List<string> keys = new List<string>(map.Keys);
        foreach (string key in keys)
        {
            string[] row = map[key];
            if (row.Length < 27)
            {
                string[] padded = new string[27];
                row.CopyTo(padded, 0);
                map[key] = padded;
            }
        }
        string name = CultureInfo.CurrentUICulture.Name;
        string[] s;
        if (map.TryGetValue(name, out s)) return s;
        string baseName = name.Split('-')[0];
        foreach (string key in order)
        {
            if (key.Split('-')[0] == baseName) return map[key];
        }
        return map["en"];
    }

    [STAThread]
    static void Main()
    {
        bool createdNew;
        using (Mutex mutex = new Mutex(true, "PuriPuly-Heart-Portable-Launcher", out createdNew))
        {
        if (!createdNew) return;
        AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
        AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
        string root = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
        Environment.SetEnvironmentVariable("UV_PROJECT_ENVIRONMENT", Path.Combine(root, "runtime", ".venv"));
        T = Tr();
        InitColors(IsDarkTheme());
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        Form form = new Form();
        form.Text = "PuriPuly Heart";
        form.FormBorderStyle = FormBorderStyle.FixedDialog;
        form.StartPosition = FormStartPosition.CenterScreen;
        form.Size = new Size(400, 340);
        form.BackColor = BG;
        form.MaximizeBox = false;
        form.MinimizeBox = false;
        form.AutoScaleMode = AutoScaleMode.Font;

        ToolTip tip = new ToolTip();

        Label titleLabel = new Label();
        titleLabel.Text = "\u2665  PuriPuly Heart";
        titleLabel.Font = new Font("Segoe UI", 18, FontStyle.Bold);
        titleLabel.ForeColor = ACCENT_PINK;
        titleLabel.AutoSize = false;
        titleLabel.Size = new Size(380, 36);
        titleLabel.Location = new Point(10, 10);
        titleLabel.TextAlign = ContentAlignment.MiddleCenter;

        Label versionLabel = new Label();
        versionLabel.Text = "app v?  \u00B7  launcher v" + LauncherVersion;
        versionLabel.Font = new Font("Segoe UI", 9);
        versionLabel.ForeColor = FG3;
        versionLabel.AutoSize = false;
        versionLabel.Size = new Size(380, 18);
        versionLabel.Location = new Point(10, 46);
        versionLabel.TextAlign = ContentAlignment.MiddleCenter;

        Label sep = new Label();
        sep.AutoSize = false;
        sep.Size = new Size(360, 1);
        sep.Location = new Point(20, 70);
        sep.BackColor = FG3;

        Label pythonLabel = new Label();
        pythonLabel.Text = "Python: ?  (portable)";
        pythonLabel.Font = new Font("Segoe UI", 9);
        pythonLabel.ForeColor = FG3;
        pythonLabel.AutoSize = false;
        pythonLabel.Size = new Size(380, 18);
        pythonLabel.Location = new Point(10, 72);
        pythonLabel.TextAlign = ContentAlignment.MiddleCenter;

        int btnW = 360, btnH = 38, btnGap = 6;

        Panel mainPanel = new Panel();
        mainPanel.Size = new Size(btnW, 180);
        mainPanel.Location = new Point(20, 92);
        mainPanel.BackColor = BG;

        Button btnLaunch = new Button();
        btnLaunch.Text = "\u25B6  " + T[1];
        btnLaunch.Font = new Font("Segoe UI", 13, FontStyle.Bold);
        btnLaunch.Size = new Size(btnW, 44);
        btnLaunch.Location = new Point(0, 0);
        btnLaunch.FlatStyle = FlatStyle.Flat;
        btnLaunch.BackColor = BG3;
        btnLaunch.ForeColor = ACCENT_GREEN;
        btnLaunch.TextAlign = ContentAlignment.MiddleCenter;
        btnLaunch.FlatAppearance.BorderColor = ACCENT_GREEN;
        btnLaunch.Click += delegate(object sender, EventArgs e) { Launch(root, false, form); };
        tip.SetToolTip(btnLaunch, T[8]);

        Button btnDebug = new Button();
        btnDebug.Text = "\u26A1  " + T[2];
        btnDebug.Font = new Font("Segoe UI", 11);
        btnDebug.Size = new Size(btnW, btnH);
        btnDebug.Location = new Point(0, 50);
        btnDebug.FlatStyle = FlatStyle.Flat;
        btnDebug.BackColor = BG3;
        btnDebug.ForeColor = Color.FromArgb(255, 180, 80);
        btnDebug.TextAlign = ContentAlignment.MiddleLeft;
        btnDebug.FlatAppearance.BorderColor = Color.FromArgb(255, 180, 80);
        btnDebug.Click += delegate(object sender, EventArgs e) { Launch(root, true, form); };
        tip.SetToolTip(btnDebug, T[9]);

        Button btnMaint = new Button();
        btnMaint.Text = "\u2699  " + T[3];
        btnMaint.Font = new Font("Segoe UI", 11);
        btnMaint.Size = new Size(btnW, btnH);
        btnMaint.Location = new Point(0, 50 + btnH + btnGap);
        btnMaint.FlatStyle = FlatStyle.Flat;
        btnMaint.BackColor = BG3;
        btnMaint.ForeColor = ACCENT_BLUE;
        btnMaint.TextAlign = ContentAlignment.MiddleLeft;
        btnMaint.FlatAppearance.BorderColor = ACCENT_BLUE;
        tip.SetToolTip(btnMaint, T[10]);

        Button btnExit = new Button();
        btnExit.Text = "\u2715  " + T[4];
        btnExit.Font = new Font("Segoe UI", 11);
        btnExit.Size = new Size(btnW, btnH);
        btnExit.Location = new Point(0, 50 + (btnH + btnGap) * 2);
        btnExit.FlatStyle = FlatStyle.Flat;
        btnExit.BackColor = BG3;
        btnExit.ForeColor = ACCENT_RED;
        btnExit.TextAlign = ContentAlignment.MiddleLeft;
        btnExit.FlatAppearance.BorderColor = ACCENT_RED;
        btnExit.Click += delegate(object sender, EventArgs e) { form.Close(); };
        tip.SetToolTip(btnExit, T[11]);

        mainPanel.Controls.Add(btnLaunch);
        mainPanel.Controls.Add(btnDebug);
        mainPanel.Controls.Add(btnMaint);
        mainPanel.Controls.Add(btnExit);

        Panel maintPanel = new Panel();
        maintPanel.Size = new Size(btnW, 180);
        maintPanel.Location = new Point(20, 92);
        maintPanel.BackColor = BG;
        maintPanel.Visible = false;

        Button btnCleanData = new Button();
        btnCleanData.Text = "\u2715  " + T[6];
        btnCleanData.Font = new Font("Segoe UI", 11);
        btnCleanData.Size = new Size(btnW, btnH);
        btnCleanData.Location = new Point(0, 0);
        btnCleanData.FlatStyle = FlatStyle.Flat;
        btnCleanData.BackColor = BG3;
        btnCleanData.ForeColor = ACCENT_RED;
        btnCleanData.TextAlign = ContentAlignment.MiddleLeft;
        btnCleanData.FlatAppearance.BorderColor = ACCENT_RED;
        btnCleanData.Click += delegate(object sender, EventArgs e)
        {
            DialogResult r = MessageBox.Show(T[15], T[17], MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (r == DialogResult.Yes) ShowResult(DeleteDataUserContents(root));
        };
        tip.SetToolTip(btnCleanData, T[12]);

        Button btnCleanCache = new Button();
        btnCleanCache.Text = "\u2715  " + T[7];
        btnCleanCache.Font = new Font("Segoe UI", 11);
        btnCleanCache.Size = new Size(btnW, btnH);
        btnCleanCache.Location = new Point(0, btnH + btnGap);
        btnCleanCache.FlatStyle = FlatStyle.Flat;
        btnCleanCache.BackColor = BG3;
        btnCleanCache.ForeColor = ACCENT_YELLOW;
        btnCleanCache.TextAlign = ContentAlignment.MiddleLeft;
        btnCleanCache.FlatAppearance.BorderColor = ACCENT_YELLOW;
        btnCleanCache.Click += delegate(object sender, EventArgs e)
        {
            DialogResult r = MessageBox.Show(T[16], T[17], MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (r == DialogResult.Yes) ShowResult(DeletePythonCache(root));
        };
        tip.SetToolTip(btnCleanCache, T[13]);

        Button btnCleanCacheFolder = new Button();
        btnCleanCacheFolder.Text = "✕  " + T[22];
        btnCleanCacheFolder.Font = new Font("Segoe UI", 11);
        btnCleanCacheFolder.Size = new Size(btnW, btnH);
        btnCleanCacheFolder.Location = new Point(0, (btnH + btnGap) * 2);
        btnCleanCacheFolder.FlatStyle = FlatStyle.Flat;
        btnCleanCacheFolder.BackColor = BG3;
        btnCleanCacheFolder.ForeColor = Color.FromArgb(255, 180, 80);
        btnCleanCacheFolder.TextAlign = ContentAlignment.MiddleLeft;
        btnCleanCacheFolder.FlatAppearance.BorderColor = Color.FromArgb(255, 180, 80);
        btnCleanCacheFolder.Click += delegate(object sender, EventArgs e)
        {
            DialogResult r = MessageBox.Show(T[24], T[17], MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (r == DialogResult.Yes) ShowResult(DeleteCacheContents(root));
        };
        tip.SetToolTip(btnCleanCacheFolder, T[23]);

        Button btnBack = new Button();
        btnBack.Text = "\u25C0  " + T[5];
        btnBack.Font = new Font("Segoe UI", 11);
        btnBack.Size = new Size(btnW, btnH);
        btnBack.Location = new Point(0, (btnH + btnGap) * 3);
        btnBack.FlatStyle = FlatStyle.Flat;
        btnBack.BackColor = BG3;
        btnBack.ForeColor = ACCENT_BLUE;
        btnBack.TextAlign = ContentAlignment.MiddleLeft;
        btnBack.FlatAppearance.BorderColor = ACCENT_BLUE;
        tip.SetToolTip(btnBack, T[14]);

        maintPanel.Controls.Add(btnCleanData);
        maintPanel.Controls.Add(btnCleanCache);
        maintPanel.Controls.Add(btnCleanCacheFolder);
        maintPanel.Controls.Add(btnBack);

        btnMaint.Click += delegate(object sender, EventArgs e)
        {
            mainPanel.Visible = false;
            maintPanel.Visible = true;
            form.AcceptButton = null;
        };
        btnBack.Click += delegate(object sender, EventArgs e)
        {
            maintPanel.Visible = false;
            mainPanel.Visible = true;
            form.AcceptButton = btnLaunch;
        };

        form.Controls.Add(titleLabel);
        form.Controls.Add(versionLabel);
        form.Controls.Add(sep);
        form.Controls.Add(pythonLabel);
        form.Controls.Add(mainPanel);
        form.Controls.Add(maintPanel);
        form.AcceptButton = btnLaunch;

        form.Shown += delegate(object sender, EventArgs e)
        {
            ThreadPool.QueueUserWorkItem(delegate(object state)
            {
                string av = ReadAppVersion(root);
                string pv = ReadPythonVersion(root);
                try
                {
                    form.BeginInvoke(new MethodInvoker(delegate()
                    {
                        versionLabel.Text = "app v" + av + "  ·  launcher v" + LauncherVersion;
                        pythonLabel.Text = "Python: " + pv + "  (portable)";
                    }));
                }
                catch { }
            });
        };

        Application.Run(form);

        if (startedProc != null && !startedDebug)
        {
            try
            {
                if (startedProc.WaitForExit(4000) && startedProc.ExitCode != 0)
                {
                    string crash = (T[25] == null || T[25].Length == 0) ? "Launch error (code {0})." : T[25];
                    MessageBox.Show(crash.Replace("{0}", startedProc.ExitCode.ToString()), "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                startedProc.Dispose();
            }
            catch { }
        }
        }
    }
    static void Launch(string root, bool debug, Form form)
    {
        string python = Path.Combine(root, "runtime", "python", debug ? "python.exe" : "pythonw.exe");
        if (!File.Exists(python))
        {
            MessageBox.Show(T[20] + " " + python, "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = python;
        psi.WorkingDirectory = root;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = !debug;
        psi.Arguments = debug ? "-m puripuly_heart.main --debug-ui-preview" : "-m puripuly_heart.main";

        try
        {
            startedProc = Process.Start(psi);
            startedDebug = debug;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, T[21], MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        form.Close();
    }

    static void ShowResult(int errors)
    {
        string msg = errors == 0 ? T[18] : T[19] + " " + errors + "\n\n" + T[26];
        MessageBox.Show(msg, "PuriPuly Heart", MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    static int DeleteDataUserContents(string root)
    {
        int errors = 0;
        string dir = Path.Combine(root, "var", "data_user");
        try
        {
            if (Directory.Exists(dir))
            {
                foreach (string f in Directory.GetFiles(dir, "*", SearchOption.TopDirectoryOnly))
                {
                    try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); }
                    catch { errors++; }
                }
                foreach (string d in Directory.GetDirectories(dir, "*", SearchOption.TopDirectoryOnly))
                {
                    try { Directory.Delete(d, true); }
                    catch { errors++; }
                }
            }
        }
        catch { errors++; }
        return errors;
    }

    static int DeletePythonCache(string root)
    {
        int errors = 0;
        try
        {
            foreach (string d in Directory.GetDirectories(root, "__pycache__", SearchOption.AllDirectories))
            {
                try { Directory.Delete(d, true); }
                catch { errors++; }
            }
        }
        catch { errors++; }
        return errors;
    }

    static int DeleteCacheContents(string root)
    {
        int errors = 0;
        string dir = Path.Combine(root, "var", "cache");
        try
        {
            if (Directory.Exists(dir))
            {
                foreach (string f in Directory.GetFiles(dir, "*", SearchOption.TopDirectoryOnly))
                {
                    try { File.SetAttributes(f, FileAttributes.Normal); File.Delete(f); }
                    catch { errors++; }
                }
                foreach (string d in Directory.GetDirectories(dir, "*", SearchOption.TopDirectoryOnly))
                {
                    try { Directory.Delete(d, true); }
                    catch { errors++; }
                }
            }
        }
        catch { errors++; }
        return errors;
    }

    static string ReadAppVersion(string root)
    {
        try
        {
            string path = Path.Combine(root, "pyproject.toml");
            foreach (string line in File.ReadAllLines(path))
            {
                string t = line.Trim();
                if (t.StartsWith("version ="))
                {
                    int q1 = t.IndexOf('"');
                    int q2 = t.LastIndexOf('"');
                    if (q1 >= 0 && q2 > q1) return t.Substring(q1 + 1, q2 - q1 - 1);
                }
            }
        }
        catch { }
        return "?";
    }

    static string ReadPythonVersion(string root)
    {
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = Path.Combine(root, "runtime", "python", "python.exe");
            psi.Arguments = "--version";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            using (Process proc = Process.Start(psi))
            {
                string s = proc.StandardOutput.ReadToEnd().Trim();
                proc.WaitForExit();
                if (s.StartsWith("Python ")) s = s.Substring(7);
                return s.Length > 0 ? s : "?";
            }
        }
        catch { return "?"; }
    }
}

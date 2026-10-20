# مُعرِّب (v0.4.0)

## الهيكل (ثابت من الآن؛ التحديثات القادمة تُضاف فوقه)

    arabizer.py            نقطة الدخول (الواجهة / --release / --version)
    arabizer_core/         نواة التطبيق
        checks.py          طرق التحقق من اللعبة (المسار، exe، Steam، الكتابة، المحرك)
        scan.py            كشف ملفات النص + الفحص الكامل
        patcher.py         تطبيق التعريب والتراجع (manifest + نسخ احتياطية + تراجع تلقائي)
        library.py         مكتبة الألعاب المعرّبة (تُحفظ في ~/.arabizer/library.json)
        updater.py         التحديث عن بعد (متعدد الملفات + SHA-256 + نسخة احتياطية)
        config.py          الإعدادات وسجل الأعطال
        gui.py             الواجهة
    ndformats/             مكتبة قراءة الأرشيفات (DSAR / PSAR) وسطر أوامرها
    tests/                 اختبارات الوحدات
    check_install.py       فحص سلامة التثبيت
    dsar_reader.py         غلاف توافق لسطر الأوامر (يعمل من أي مجلد)

## التشغيل
    python check_install.py      (تأكد أولاً)
    python arabizer.py           (الواجهة)

## قارئ الأرشيفات (سطر الأوامر)
لا يعدّل ملفات اللعبة أبداً، ومجلد الإخراج لا يجوز أن يكون مجلد اللعبة.
شريط التقدم سطر واحد متحرك (بدل مئات الأسطر).

    python -m ndformats inspect "<bin.psarc>" --deep --json report.json
    python -m ndformats unpack  "<bin.psarc>" --out "D:\tlou_out"
    python -m ndformats extract "<bin.psarc>" --out "D:\tlou_files"
    python -m ndformats names   "<bin.psarc>" --grep loc
    python -m ndformats selftest

إذا ظهر "No module named ndformats" فأنت في مجلد غير مجلد البرنامج؛ استخدم المسار الكامل:

    python "C:\...\arabizer\dsar_reader.py" inspect "<bin.psarc>" --deep

رموز الخروج: 0 سليم / 1 فشل / 3 ناتج غير موثّق (لا تعتمده)

## نشر تحديث (من جهازك)
1. عدّل `__version__` في arabizer_core/__init__.py.
2. `python arabizer.py --release "ملاحظات الإصدار"` (ينشئ version.json).
3. ارفع الملفات المعدلة + version.json إلى GitHub (Upload files).
4. داخل البرنامج: تبويب التحديثات > تحقق من التحديث > تحديث الآن.

## لو انهار البرنامج
    Get-Content "$env:USERPROFILE\.arabizer\last_step.txt"
    Get-Content "$env:USERPROFILE\.arabizer\crash.log" -Tail 40

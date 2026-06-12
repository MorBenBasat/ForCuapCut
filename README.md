# ForCuapCut — יצירת סליידים לטיקטוק

אוטומציה ליצירת תמונות שיר (1080×1920) בסגנון CapCut — רקע גיטרה, תמונת זמר, אקורדים וטקסט.

## התקנה

```bash
pip install -r requirements.txt
```

## הכנת קבצים

שים את הקבצים שלך בתיקיות:

```
assets/
  backgrounds/guitar.jpg    ← תמונת רקע
  chords/Em.png, C.png...   ← כל האקורדים
input/
  dudu_aharon.jpg           ← תמונת זמר לכל שיר
output/                     ← התמונות שנוצרות
```

לדוגמה מהירה (תמונות placeholder):

```bash
python create_test_assets.py
```

## שימוש מהיר (מומלץ)

### זמרים שמורים

ב-`config.json` יש רשימת זמרים → תמונה. לא צריך לחזור על `artist_image` בכל שיר.

```json
"artists": {
  "דודו אהרון": "input/dudu_aharon.png",
  "אודיה": "input/Odeya.png"
}
```

זמר חדש? הוסף שורה אחת כאן + תמונה ב-`input/`.

### הוספת שיר דרך Cursor (הכי פשוט)

פתח צ'אט ב-**Agent** וכתוב משפט אחד, למשל:

```
תוסיף שיר לדודו אהרון: "לילה טוב", אקורדים Em,C,G,D
```

Cursor יוצר את הקובץ, מריץ `generate.py`, ונותן לך תמונה ב-`output/`.

אפשר גם לציין `@add-song-slide` בצ'אט — אותו דבר.

### שורת פקודה (בלי Cursor)

```bash
python generate.py --artist "דודו אהרון" --song "לילה טוב" --chords Em,C,G,D
```

## שימוש

### סלייד פתיחה (Intro — התמונה הראשונה)

ערוך `intros/example.yaml` והרץ:

```bash
python generate.py intros/example.yaml
```

```yaml
line1: "3 שירים מוכרים"      # לבן, למעלה
line2: "של 4 אקורדים"         # צהוב, מתחת לשורה 1
line3: "5 דקות ללמוד"         # לבן, למטה
line4: "אקורדים בסיסיים בלבד" # צהוב, למטה (אופציונלי)
output: "output/intro.png"
```

מינימום 3 שורות (`line1`–`line3`). `line4` אופציונלי.

### סלייד שיר — קובץ YAML

העתק `songs/_template.yaml`, שנה שם/אקורדים, והרץ:

```bash
python generate.py songs/bachor_ragish.yaml
```

## פריסת אקורדים

| מספר אקורדים | פריסה |
|--------------|--------|
| 4 | 2×2 ממורכז |
| 5 | 3 למעלה + 2 למטה |
| 6 | 3×2 (שתי שורות של שלוש) |

מיקומים מ-CapCut נמצאים ב-`config.json` — אפשר לכוונן שם.

## כיוונון

ערוך `config.json` אם צריך להזיז אלמנטים:

- **זמר:** `singer` — X:112, Y:219, Scale:21%
- **אקורדים:** `chord_layouts` — מיקום ו-scale לכל פריסה
- **טקסט:** `text.artist` (קטן יותר) ו-`text.song` (גדול יותר)

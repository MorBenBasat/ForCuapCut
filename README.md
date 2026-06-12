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

## שימוש

### קובץ YAML (מומלץ)

ערוך `songs/bachor_ragish.yaml` והרץ:

```bash
python generate.py songs/bachor_ragish.yaml
```

### שורת פקודה

```bash
python generate.py --artist "דודו אהרון" --song "בחור רגיש" --chords Em,C,G,D --artist-image input/dudu_aharon.jpg
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

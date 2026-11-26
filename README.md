# Reis

Windows için sade, yerel ve token-tasarruflu sesli masaüstü asistanı. Ekranın sağında ince bir kenar çubuğu olarak durur: üstte hava durumu ve sistem kullanımı, ortada duruma göre renk değiştiren dönen küre, altında **Projeler / Günlük Plan / Program** düğmeleri. Notlarını (Markdown) ayrı, büyük yazılı bir pencerede sekmeler hâlinde açar; sorularına en fazla ~30 kelimelik kısa bir sesli cevap verir.

*English: a minimal, local-first, token-frugal voice assistant sidebar for Windows (PySide6 + faster-whisper + Gemini free tier). Reads your Markdown notes, answers in one short spoken sentence. MIT licensed.*

## Özellikler

- **Kenar çubuğu:** Elazığ (ayarlanabilir) hava durumu, CPU/RAM/disk, dönen küre, mesaj kutusu. Daraltınca ekranın sağında küçük bir kare kalır, tıklayınca açılır.
- **Notlar:** Projeler, Günlük Plan ve Program düğmeleri ayrı bir pencerede sekmeler olarak açar (16 pt, Esc ile kapanır). Görev satırına tıklamak dosyada `[x] ~~metin~~` yazar. Plan ve programa uygulamadan madde eklenip silinebilir.
- **Şifreler:** `Ad: şifre` satırlı düz metin dosyasından liste; tek tıkla kopyalama (30 sn sonra pano temizlenir), ekleme, silme.
- **F1:** sesle dinle (susunca kendiliğinden biter). **F2:** asistana otomatik "Reis günaydın, brifing ver" mesajı gönderir.
- **Token tasarrufu:** "günlük planı göster", "programı aç" gibi komutlar yerelde çözülür (0 token). Diğerleri tek küçük Gemini çağrısıyla yönlendirilir. Not içerikleri LLM'e gönderilmez, yalnızca ilgili özet bölümü.
- **Hafif:** gizliyken/daraltılmışken animasyon ve sistem zamanlayıcıları durur; Whisper ve ses motoru kullanılmayınca belleği geri verir.
- **Açılışta başlar** (Ayarlar'dan kapatılır).

## Kurulum (kaynaktan)

Python 3.11+ gerekir.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m reis
```

İlk açılışta kurulum sihirbazı çıkar (Gemini anahtarı, not yolları, ses, Whisper modeli). Hepsi atlanabilir, sonra Ayarlar'dan (⚙) yapılabilir.

**Gemini anahtarı:** https://aistudio.google.com adresinden ücretsiz alınır; Ayarlar > Gemini API anahtarı. Windows Credential Manager'da saklanır, dosyaya yazılmaz.

## Nasıl kullanılır

1. **Aç:** Reis ekranın sağında ince bir çubuk olarak durur. `»` ile küçültürsün (sağda küçük bir kare kalır), kareye tıklayınca geri açılır.
2. **Konuş ya da yaz:** `F1`'e bas, konuş, sus; Reis işler ve kısa cevap verir. İstersen alttaki kutuya yaz. `F2` "günaydın, brifing ver" der: günün planını özetler.
3. **Notların:** Dört düğme var.
   - **PROJELER:** her proje bir sekme olarak büyük pencerede açılır.
   - **GÜNLÜK PLAN / PROGRAM:** bir göreve tıklayınca tamamlanır (üstü çizilir). Altta yazıp **Ekle** dersen madde dosyaya eklenir, **Sil…** ile seçip silersin. Dosyayı elle açmana gerek yok.
   - **ŞİFRELER:** açarken Windows seni doğrular (Windows Hello/PIN, yoksa Windows parolan). Sonra kayıtlı şifrelerin listesi gelir. **Kopyala**'ya bas, şifre panoya gider ve 30 sn sonra kendiliğinden silinir. "Göster" işaretli değilken şifreler gizli durur. Aşağıdan yeni şifre eklenir, **Sil** ile kaldırılır. İlk açılışta dosyayı şifrelemeyi önerir (**Şifrele** düğmesi): şifrelenince dosya yalnızca senin Windows hesabında okunur, Not Defteri ile açılmaz; yedek için **Yedek (düz metin)** var.
4. **Ayarlar (⚙):** Gemini anahtarı, ses, şehir, hitap ("patron" yazarsan sana öyle seslenir), kısayollar ve dosya yolları buradadır.

### Dosyalar

Hepsi düz metin; Reis yalnızca ihtiyaç olduğunda yazar. Örnek şablonlar `ornek/` klasöründe:

| Dosya | Şablon | Biçim |
| --- | --- | --- |
| Şifreler | `ornek/sifreler.ornek.txt` | Her satır `Ad: şifre` (ör. `E-Devlet: xxxxxx`) |
| Günlük plan | `ornek/gunluk-plan.ornek.md` | Markdown, görevler `- [ ] metin` |
| Program | `ornek/program.ornek.md` | Her gün bir `## Pazartesi` başlığı, altında `- 09:00 Ders` |
| Proje notu | klasörde `asistannote.md` | Üstte kısa özet, sonra görevler |

Şablonu kopyalayıp kendi yoluna koy, sonra Ayarlar'dan (⚙ > Genel / Notlar) yolunu göster. Günlük plan için tek dosya yerine klasör de verebilirsin; adında günün tarihi geçen dosya (`2026-11-26.md` gibi) bugünün planı sayılır, yoksa **Ekle** onu kendisi oluşturur.

> Şifre dosyasını **Şifrele** ile korumazsan düz metin kalır. Şifreli de olsa kendi bilgisayarında tut, GitHub'a ya da buluta yükleme.

## Ses (TTS)

Sağlayıcılar: **yazgı-tts-1** (Apache-2.0, çevrimdışı, ayrı Python ortamında; Ayarlar > Ses > "Ses motorunu kur", ~1 GB indirme), **Edge TTS** (resmî olmayan uç nokta), **Gemini TTS**, **Piper** (çevrimdışı, ~60 MB), **ElevenLabs** (kendi API anahtarınla; Ayarlar > Ses > ElevenLabs: anahtar + voice ID + model. Anahtar Credential Manager'da saklanır. Kredi koruması: uzun metni kısaltır, kredi bitince Edge'e düşer). Zincir: birincil → Edge → Piper; her cümle için her biri en çok bir kez denenir. Altyazı özgün metni gösterir; saat/sayılar seslendirmeden önce Türkçe okunuşa çevrilir.

Önemli ayarlar (`%APPDATA%\Reis\settings.json`):

| Ayar | Varsayılan | Anlamı |
| --- | --- | --- |
| `city` | `Elazığ` | Hava durumu şehri (servise yalnızca şehir adı gider) |
| `hotkey` / `briefing_hotkey` | `F1` / `F2` | Dinleme / brifing kısayolları |
| `autostart` | `true` | Windows açılışında başlat |
| `yazgi_prewarm_on_start` | `false` | `true`: açılışta ses motorunu yükle (~0,9 GB RAM) |
| `yazgi_idle_minutes` | `10` | Ses motoru bu kadar dk boşta kalırsa kapanır; `0` = kapanmasın |

Ortam değişkenleri (isteğe bağlı): `REIS_DATA_DIR` (ayar/model/önbelleği tek klasöre yönlendirir, test için), `REIS_MODELS_DIR`, `REIS_RUNTIME_DIR`, `REIS_YAZGI_THREADS`.

## Paketleme

```powershell
.\build.ps1          # çıktı: dist\Reis\Reis.exe (PyInstaller, onedir)
```

Modeller pakete gömülmez, ilk kullanımda indirilir. Paketlenmiş sürümde yazgı kurulamaz (sistem Python'u yok); Edge/Piper kullanılır.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Testler gerçek ağ, model indirme, mikrofon, hoparlör, Gemini ve gerçek `Run` kaydı kullanmaz.

## Lisans

MIT (bkz. `LICENSE`). Üçüncü taraf bileşenler: `THIRD_PARTY.md`.

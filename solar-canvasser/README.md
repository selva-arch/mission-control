# Solar Canvasser

Scan a suburb, find the roofs **without** solar panels, render panels onto each
one, estimate the bill savings, and produce a printable per-house flyer to mail
the homeowner.

Built for a **solar installer generating their own leads**. MVP target suburb:
**Dulwich, SA 5065**.

> Status: scaffold. The pipeline shape, the swappable imagery layer, the savings
> engine and the flyer generator are real; the keyed Google calls and the
> detection model have clearly-marked integration points (see ARCHITECTURE.md).

## The pipeline

```
suburb ─▶ addresses ─▶ imagery ─▶ detect panels ─▶ keep "no solar"
                                                        │
        flyer PDF ◀─ savings $ ◀─ roof sizing ◀─ render panels
            │
            ▼
        address + post
```

| Stage | Module | Source / method |
|-------|--------|-----------------|
| Suburb → addresses | `pipeline/addresses.py` | OpenStreetMap Overpass (free) or G-NAF |
| Aerial tile per roof | `pipeline/imagery.py` | **Swappable**: Google Static (default), licensed, or local |
| Has-panels? | `pipeline/detect.py` | Vision-model classifier (MVP) → CNN (scale) |
| Roof size / kW / kWh | `pipeline/solar.py` | Google **Solar API** building insights |
| Render panels on roof | `pipeline/render.py` | Compositing (MVP) → AI image edit |
| Est. $ saved / year | `pipeline/savings.py` | Generation × tariff (reuses the battery tariff model) |
| Printable flyer | `pipeline/flyer.py` | HTML → PDF, one per house |
| Address + mail export | `pipeline/mail.py` | Australia Post addressed/unaddressed |

## Setup

```bash
cd solar-canvasser
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml      # then add your Google Maps Platform key
```

## Run

```bash
# Validate the whole chain on ONE address first (recommended before spending postage):
python run_one.py --address "1 Rundle St, Dulwich SA 5065"

# Full suburb batch -> per-house flyers + a mail-merge CSV:
python run_suburb.py --suburb "Dulwich, SA"
```

## Costs (rough, for ~1,400 Dulwich dwellings)

- **Google APIs** (geocode + static imagery + Solar API + vision detect): ~$0.05–0.15/address → **~$70–210 total**.
- **Print + postage**: ~$0.60 (unaddressed) to ~$1.10 (addressed) per item → **~$850–1,550** — *this dominates*.
- Because SA has ~40% existing rooftop solar, good detection removes ~40% of the mail volume → the single biggest lever on campaign ROI.

## Important: imagery licensing

Google Maps/Static imagery is great for detection and prototyping, but Google's
terms restrict using their imagery **inside printed material mailed to third
parties**. The imagery layer is deliberately swappable (`ImagerySource`) so the
*printed* flyer can use a properly licensed or self-captured image while you
keep Google for the cheap detection pass. Confirm your licence before a real
mail-out. See `ARCHITECTURE.md → Compliance`.

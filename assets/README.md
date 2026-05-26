# IBM Brand Assets

Curated subset extracted from the IBM brand-cover .potx templates (see `research/ibm_brand/pdfs/` for the source PDFs and `~/Downloads/IBM_presentation_template_PowerPoint/03_IBM_presentation_brand_covers_PowerPoint/` for the .potx originals). Partner Plus assets intentionally excluded.

**Aspect ratio**: all covers are widescreen (16:9), matching the standard 13.333 × 7.5 in slide canvas. Logo is roughly 3:1.

## Logos

| File | Use |
|---|---|
| `logos/ibm_8bar_blue.png` | IBM 8-bar logo in Blue 60 `#0F62FE`. Hero placement on white / light covers, or as the bottom-right mark on content slides. PNG with transparent background. |

A black variant is needed for use on white slides where the Blue 60 mark would feel too saturated; it's not in the brand-cover .potx as a standalone asset — derivable in the next pass by recoloring or by extracting from the main template .potx.

## Brand covers

Full-canvas 16:9 cover compositions. Use as backgrounds for cover slides and `end_slide_blank`; can also be applied as the right-band imagery on `case_study_card` or section dividers.

| File | Tone | Best for |
|---|---|---|
| `covers/cover_3d_cityscape_blue.png` | Confident, modern, modernist Blue 60 isometric architecture | Sales pitch, product launch, roadmap |
| `covers/cover_3d_letters_blue.png` | Bold, branded — 3D Blue 60 IBM letters as architecture | Hero brand cover when "IBM" is the message |
| `covers/cover_8bar_chromatic.png` | Vibrant, multi-color mosaic of IBM letters | Creative / culture / hackathon / innovation context |
| `covers/cover_dots_minimal_white.png` | Soft, minimal — IBM in white pill shapes on light gray | Quieter case studies, customer-first narratives |
| `covers/cover_embossed_light.png` | Elegant, pale embossed IBM letters on light blue | Executive briefings, refined / advisory tone |
| `covers/cover_geometric_inspired.jpeg` | Bold sculpture, white + Blue 60 isometric forms | Architecture / technical decks, modernization stories |
| `covers/cover_consulting_circles.png` | Cyan circles on grid (Consulting sub-brand) | IBM_Consulting decks |
| `covers/cover_quantum_hardware.jpeg` | Silver chip hardware photography (Quantum sub-brand) | IBM_Quantum decks |

## How the synthesis prompt references these

In `deck.json`, the optional `available_assets` field exposes the asset library to the coder:

```json
{
  "available_assets": {
    "logos": { "ibm_blue": "assets/logos/ibm_8bar_blue.png" },
    "brand_covers": {
      "3d_cityscape_blue":  "assets/covers/cover_3d_cityscape_blue.png",
      "8bar_chromatic":     "assets/covers/cover_8bar_chromatic.png",
      "geometric_inspired": "assets/covers/cover_geometric_inspired.jpeg"
    }
  }
}
```

Coder uses `slide.addImage({path: "assets/covers/cover_3d_cityscape_blue.png", ...})` for full-bleed cover backgrounds, with text overlaid on top.

## Adding more assets

The staging archive (`staging/`) has been deleted after curation. To extract more:

```bash
cd research/ibm_brand/assets && mkdir -p staging && cd staging
unzip ~/Downloads/IBM_presentation_template_PowerPoint/03_IBM_presentation_brand_covers_PowerPoint/IBM_presentations_brand_covers_v_2_1_Plex.potx "ppt/media/*"
# inspect, then copy candidates to ../covers/ with a descriptive name
```

The main brand .potx has 21 PNG candidates + 6 JPEG; we curated 6. The full pool is in the source .potx if a recipe calls for a treatment we don't yet cover.

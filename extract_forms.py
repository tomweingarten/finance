import pikepdf
import sys

files = [
    '/home/tom/projects/finance/Tax_Documents/2025/Forms/f1040_PRELIMINARY.pdf',
    '/home/tom/projects/finance/Tax_Documents/2025/Forms/f1040sb_PRELIMINARY.pdf',
    '/home/tom/projects/finance/Tax_Documents/2025/Forms/f1040sd_PRELIMINARY.pdf',
    '/home/tom/projects/finance/Tax_Documents/2025/Forms/f1040se_PRELIMINARY.pdf',
    '/home/tom/projects/finance/Tax_Documents/2025/Forms/it201_PRELIMINARY.pdf',
]

for f in files:
    print(f'\n{"="*80}')
    print(f'FILE: {f.split("/")[-1]}')
    print(f'{"="*80}')
    pdf = pikepdf.Pdf.open(f)
    for i, page in enumerate(pdf.pages):
        annots = page.get(pikepdf.Name.Annots)
        if annots is None:
            continue
        for annot_ref in annots:
            try:
                name = str(annot_ref.get(pikepdf.Name.T, ''))
                val = annot_ref.get(pikepdf.Name.V, None)
                if val is not None:
                    vs = str(val)
                    if vs.strip() and vs != '/Off' and vs != 'None':
                        print(f'  {name}: {vs}')
            except Exception as e:
                pass
    pdf.close()
    sys.stdout.flush()

print("\nDONE")

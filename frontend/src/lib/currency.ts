const USD_FORMATTER = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatUsdCurrency(value: unknown) {
  if (value == null || value === '') return '';
  if (typeof value !== 'string' && typeof value !== 'number') return '';

  const numericValue = typeof value === 'number'
    ? value
    : Number(value.replaceAll(',', '').replaceAll('$', ''));
  return Number.isFinite(numericValue) ? USD_FORMATTER.format(numericValue) : '';
}

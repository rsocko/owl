import { useState } from 'react';
import { formatUsdCurrency } from '../lib/currency';

function sanitizeCurrencyInput(value: string, allowNegative: boolean) {
  const negative = allowNegative && value.trimStart().startsWith('-');
  const cleaned = value.replace(/[^0-9.]/g, '');
  const [whole = '', ...fractionParts] = cleaned.split('.');
  const numericValue = fractionParts.length ? `${whole}.${fractionParts.join('')}` : whole;
  return negative ? `-${numericValue}` : numericValue;
}

export default function CurrencyInput({
  value,
  onChange,
  label = 'Amount',
  disabled = false,
  allowNegative = false,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  disabled?: boolean;
  allowNegative?: boolean;
  className?: string;
}) {
  const [focused, setFocused] = useState(false);

  return (
    <input
      aria-label={label}
      className={className}
      inputMode="decimal"
      value={focused ? value : formatUsdCurrency(value) || value}
      disabled={disabled}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={(event) => onChange(sanitizeCurrencyInput(event.target.value, allowNegative))}
    />
  );
}

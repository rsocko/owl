export type AccountIdentifierClass =
  | 'provider_account'
  | 'service_account'
  | 'member'
  | 'policy'
  | 'bank_account'
  | 'payment_card';

export const ACCOUNT_IDENTIFIER_CLASSES: {
  value: AccountIdentifierClass;
  label: string;
}[] = [
  { value: 'service_account', label: 'Service account' },
  { value: 'provider_account', label: 'Provider account' },
  { value: 'member', label: 'Member ID' },
  { value: 'policy', label: 'Policy ID' },
  { value: 'bank_account', label: 'Bank account' },
  { value: 'payment_card', label: 'Payment card' },
];

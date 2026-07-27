import { describe, expect, it, beforeAll } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SortableTable, type SortableColumnDef } from './SortableTable';

// Radix Popover uses ResizeObserver which isn't available in jsdom
beforeAll(() => {
  if (!globalThis.ResizeObserver) {
    globalThis.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  }
});

interface TestRow {
  id: number;
  name: string;
  type: string;
  amount: number;
}

const testData: TestRow[] = [
  { id: 1, name: 'Alpha', type: 'PAY', amount: 100 },
  { id: 2, name: 'Beta', type: 'REVIEW', amount: 50 },
  { id: 3, name: 'Gamma', type: 'PAY', amount: 200 },
  { id: 4, name: 'Delta', type: 'FILE', amount: 75 },
];

const columns: SortableColumnDef<TestRow>[] = [
  {
    id: 'name',
    header: 'Name',
    accessorFn: (row) => row.name,
    cell: (row) => row.name,
  },
  {
    id: 'type',
    header: 'Type',
    accessorFn: (row) => row.type,
    cell: (row) => row.type,
    filterOptions: [
      { value: 'PAY', label: 'Pay' },
      { value: 'REVIEW', label: 'Review' },
      { value: 'FILE', label: 'File' },
    ],
  },
  {
    id: 'amount',
    header: 'Amount',
    accessorFn: (row) => row.amount,
    cell: (row) => `$${row.amount}`,
  },
];

describe('SortableTable', () => {
  it('renders all rows', () => {
    render(<SortableTable data={testData} columns={columns} rowKey={(r) => String(r.id)} />);
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Beta')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();
    expect(screen.getByText('Delta')).toBeTruthy();
  });

  it('renders empty state when data is empty', () => {
    render(<SortableTable data={[]} columns={columns} rowKey={(r) => String(r.id)} emptyLabel="Nothing here" />);
    expect(screen.getByText('Nothing here')).toBeTruthy();
  });

  it('sorts by column on header click', () => {
    render(<SortableTable data={testData} columns={columns} rowKey={(r) => String(r.id)} />);
    const sortBtn = screen.getByLabelText('Sort by Name');
    fireEvent.click(sortBtn);

    const cells = screen.getAllByRole('cell');
    const nameCells = cells.filter((c) => ['Alpha', 'Beta', 'Delta', 'Gamma'].includes(c.textContent ?? ''));
    expect(nameCells[0].textContent).toBe('Alpha');
    expect(nameCells[1].textContent).toBe('Beta');
    expect(nameCells[2].textContent).toBe('Delta');
    expect(nameCells[3].textContent).toBe('Gamma');
  });

  it('sorts on header click', () => {
    render(<SortableTable data={testData} columns={columns} rowKey={(r) => String(r.id)} />);
    const sortBtn = screen.getByLabelText('Sort by Amount');
    fireEvent.click(sortBtn);

    const cells = screen.getAllByRole('cell');
    const amountCells = cells.filter((c) => (c.textContent ?? '').startsWith('$'));
    // TanStack sorts numbers descending on first click
    expect(amountCells[0].textContent).toBe('$200');
    expect(amountCells[1].textContent).toBe('$100');
    expect(amountCells[2].textContent).toBe('$75');
    expect(amountCells[3].textContent).toBe('$50');
  });

  it('filters by column via filter popover', () => {
    render(<SortableTable data={testData} columns={columns} rowKey={(r) => String(r.id)} />);
    const filterBtn = screen.getByLabelText('Filter by Type');
    fireEvent.click(filterBtn);

    const payOption = screen.getByText('Pay');
    fireEvent.click(payOption);

    // Should only show PAY rows
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();
    expect(screen.queryByText('Beta')).toBeNull();
    expect(screen.queryByText('Delta')).toBeNull();
  });

  it('clears filter and shows all rows again', () => {
    render(<SortableTable data={testData} columns={columns} rowKey={(r) => String(r.id)} />);
    const filterBtn = screen.getByLabelText('Filter by Type');

    // Apply filter
    fireEvent.click(filterBtn);
    fireEvent.click(screen.getByText('Pay'));

    // Verify filtered - only PAY rows
    expect(screen.queryByText('Beta')).toBeNull();
    expect(screen.queryByText('Delta')).toBeNull();
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Gamma')).toBeTruthy();
  });

  it('does not render sort on non-sortable columns', () => {
    const noSortCols: SortableColumnDef<TestRow>[] = [
      { id: 'name', header: 'Name', cell: (r) => r.name, enableSorting: false },
    ];
    render(<SortableTable data={testData} columns={noSortCols} rowKey={(r) => String(r.id)} />);
    const btn = screen.getByLabelText('Sort by Name');
    expect(btn).toHaveAttribute('disabled');
  });
});

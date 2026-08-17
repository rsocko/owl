import { useState, useMemo, type ReactNode } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type ColumnFiltersState,
  type HeaderContext,
  type OnChangeFn,
} from '@tanstack/react-table';
import * as Popover from '@radix-ui/react-popover';
import { EmptyState } from './ui';

/**
 * Column header that renders sort indicators and an optional filter popover.
 */
function SortableHeader<T>({
  header,
  label,
  filterOptions,
}: {
  header: HeaderContext<T, unknown>;
  label: ReactNode;
  filterOptions?: { value: string; label: string }[];
}) {
  const column = header.column;
  const canSort = column.getCanSort();
  const sorted = column.getIsSorted();
  const filterValue = column.getFilterValue() as string | undefined;

  const sortIndicator = sorted === 'asc' ? ' ▲' : sorted === 'desc' ? ' ▼' : '';

  return (
    <div className="st-header">
      <button
        className="st-header-sort"
        onClick={column.getToggleSortingHandler()}
        disabled={!canSort}
        title={canSort ? 'Click to sort' : undefined}
        aria-label={`Sort by ${typeof label === 'string' ? label : 'column'}`}
      >
        <span>{label}</span>
        {canSort && <span className="st-sort-icon">{sortIndicator || ' ⇅'}</span>}
      </button>

      {filterOptions && filterOptions.length > 0 && (
        <Popover.Root>
          <Popover.Trigger asChild>
            <button
              className={`st-filter-trigger${filterValue ? ' active' : ''}`}
              aria-label={`Filter by ${typeof label === 'string' ? label : 'column'}`}
              title="Filter"
            >
              ▾
            </button>
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="st-filter-popover" sideOffset={4} align="start">
              <div className="st-filter-list">
                <button
                  className={`st-filter-option${!filterValue ? ' active' : ''}`}
                  onClick={() => column.setFilterValue(undefined)}
                >
                  All
                </button>
                {filterOptions.map((opt) => (
                  <button
                    key={opt.value}
                    className={`st-filter-option${filterValue === opt.value ? ' active' : ''}`}
                    onClick={() => column.setFilterValue(opt.value)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <Popover.Arrow className="st-filter-arrow" />
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
    </div>
  );
}

export interface SortableColumnDef<T> {
  id: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** Accessor function for sorting — should return a primitive (string | number | null) */
  accessorFn?: (row: T) => unknown;
  /** Width CSS value */
  width?: string;
  /** Enable sorting (default: true if accessorFn provided) */
  enableSorting?: boolean;
  /** Filter options to show in a dropdown. Column must have accessorFn for filtering. */
  filterOptions?: { value: string; label: string }[];
  /** Custom filter function — matches row accessor value against the filter value */
  filterFn?: (rowValue: unknown, filterValue: string) => boolean;
}

interface SortableTableProps<T> {
  data: T[];
  columns: SortableColumnDef<T>[];
  rowKey: (row: T) => string;
  emptyLabel?: string;
  sorting?: SortingState;
  onSortingChange?: OnChangeFn<SortingState>;
  columnFilters?: ColumnFiltersState;
  onColumnFiltersChange?: OnChangeFn<ColumnFiltersState>;
  /** Optional extra content above each row (e.g. checkbox column) handled via columns */
}

export function SortableTable<T>({
  data,
  columns,
  rowKey,
  emptyLabel = 'No data',
  sorting: controlledSorting,
  onSortingChange,
  columnFilters: controlledColumnFilters,
  onColumnFiltersChange,
}: SortableTableProps<T>) {
  const [internalSorting, setInternalSorting] = useState<SortingState>([]);
  const [internalColumnFilters, setInternalColumnFilters] = useState<ColumnFiltersState>([]);
  const sorting = controlledSorting ?? internalSorting;
  const columnFilters = controlledColumnFilters ?? internalColumnFilters;

  const tanstackColumns: ColumnDef<T, unknown>[] = useMemo(
    () =>
      columns.map((col) => ({
        id: col.id,
        accessorFn: col.accessorFn ?? (() => null),
        header: (ctx) => (
          <SortableHeader
            header={ctx}
            label={col.header}
            filterOptions={col.filterOptions}
          />
        ),
        cell: (info) => col.cell(info.row.original),
        enableSorting: col.enableSorting ?? !!col.accessorFn,
        filterFn: col.filterFn
          ? (row, columnId, filterValue) => {
              const cellValue = row.getValue(columnId);
              return col.filterFn!(cellValue, filterValue);
            }
          : (row, columnId, filterValue) => {
              const cellValue = String(row.getValue(columnId) ?? '').toLowerCase();
              return cellValue === String(filterValue).toLowerCase();
            },
        meta: { width: col.width },
      })),
    [columns],
  );

  const table = useReactTable({
    data,
    columns: tanstackColumns,
    state: { sorting, columnFilters },
    onSortingChange: onSortingChange ?? setInternalSorting,
    onColumnFiltersChange: onColumnFiltersChange ?? setInternalColumnFilters,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: (row) => rowKey(row),
  });

  const rows = table.getRowModel().rows;

  if (data.length === 0) {
    return <EmptyState title={emptyLabel} />;
  }

  return (
    <table className="data-table sortable-table">
      <thead>
        {table.getHeaderGroups().map((headerGroup) => (
          <tr key={headerGroup.id}>
            {headerGroup.headers.map((header) => {
              const width = (header.column.columnDef.meta as { width?: string } | undefined)?.width;
              return (
                <th key={header.id} style={{ width }}>
                  {header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={columns.length} style={{ textAlign: 'center', padding: '24px' }}>
              <span className="text-muted">No results match the current filters</span>
            </td>
          </tr>
        ) : (
          rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}

/** Active filter pills shown above the table */
export function ActiveFilters({
  columnFilters,
  columns,
  onClear,
  onClearAll,
}: {
  columnFilters: { id: string; value: unknown }[];
  columns: SortableColumnDef<unknown>[];
  onClear: (columnId: string) => void;
  onClearAll: () => void;
}) {
  if (columnFilters.length === 0) return null;

  return (
    <div className="st-active-filters">
      {columnFilters.map((f) => {
        const col = columns.find((c) => c.id === f.id);
        const option = col?.filterOptions?.find((o) => o.value === f.value);
        return (
          <span key={f.id} className="st-active-filter-pill">
            {col?.header}: {option?.label ?? String(f.value)}
            <button onClick={() => onClear(f.id)} aria-label={`Clear ${col?.header} filter`}>✕</button>
          </span>
        );
      })}
      {columnFilters.length > 1 && (
        <button className="st-clear-all" onClick={onClearAll}>Clear all</button>
      )}
    </div>
  );
}

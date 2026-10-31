//
//  ReadingListView.swift
//  PersonalOps
//
//  Detail screen for the permanent Reading List project — proper book
//  entries (title/author) in a queued → reading → finished pipeline
//  instead of generic tasks. Split into Personal Reading / Textbooks via a
//  segmented switcher; swipe leading advances a book's status, trailing
//  deletes; the context menu jumps straight to any status.
//

import GRDB
import GRDBQuery
import SwiftUI

/// Observes all books: currently-reading first, then the queue, then
/// finished (most recently finished on top).
struct BooksRequest: ValueObservationQueryable {
    static var defaultValue: [Book] { [] }

    func fetch(_ db: Database) throws -> [Book] {
        try Book.order(Book.Columns.addedAt).fetchAll(db)
    }
}

struct ReadingListView: View {
    @Query(BooksRequest()) private var books: [Book]
    @State private var category: BookCategory = .personal
    @State private var showingNewBook = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("Category", selection: $category) {
                ForEach(BookCategory.allCases, id: \.self) {
                    Text($0.displayName.uppercased()).tag($0)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)
            .padding(.top, 8)
            .padding(.bottom, 4)
            .background(NousTheme.background)

            BookCategoryList(books: books.filter { $0.category == category })
        }
        .background(NousTheme.background)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("READING LIST")
                    .font(NousTheme.pixelFontBold(size: 15, relativeTo: .headline))
                    .tracking(2)
                    .foregroundStyle(NousTheme.onBackground)
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showingNewBook = true
                } label: {
                    Image(systemName: "plus")
                        .contentShape(Rectangle())
                }
                .tint(NousTheme.onBackground)
            }
        }
        .toolbarBackground(NousTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        .sheet(isPresented: $showingNewBook) {
            NewBookSheet(defaultCategory: category)
                .presentationDetents([.medium])
        }
    }
}

// MARK: - Status-grouped list for one category

private struct BookCategoryList: View {
    let books: [Book]

    private func books(with status: BookStatus) -> [Book] {
        let matching = books.filter { $0.status == status }
        if status == .finished {
            return matching.sorted {
                ($0.finishedAt ?? .distantPast) > ($1.finishedAt ?? .distantPast)
            }
        }
        return matching
    }

    private var finishedCount: Int { books.filter { $0.status == .finished }.count }

    var body: some View {
        List {
            ForEach(BookStatus.allCases, id: \.self) { status in
                let sectionBooks = books(with: status)
                if !sectionBooks.isEmpty {
                    Section {
                        ForEach(sectionBooks) { book in
                            BookRow(book: book)
                                .listRowBackground(NousTheme.panel)
                                .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                    if let next = book.status.next {
                                        Button {
                                            DataController.shared.setBookStatus(book.id, status: next)
                                        } label: {
                                            Label(next.displayName,
                                                  systemImage: next == .finished
                                                    ? "checkmark" : "book")
                                        }
                                        .tint(next == .finished
                                            ? NousTheme.statusActive : NousTheme.accent)
                                    }
                                }
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    Button(role: .destructive) {
                                        DataController.shared.deleteBook(book.id)
                                    } label: {
                                        Label("Delete", systemImage: "trash")
                                    }
                                    .tint(NousTheme.alert)
                                }
                                .contextMenu {
                                    ForEach(BookStatus.allCases, id: \.self) { target in
                                        if target != book.status {
                                            Button {
                                                DataController.shared.setBookStatus(book.id, status: target)
                                            } label: {
                                                Label("Mark \(target.displayName)",
                                                      systemImage: target == .finished
                                                        ? "checkmark" : "book")
                                            }
                                        }
                                    }
                                }
                        }
                    } header: {
                        HStack {
                            Text(status.displayName.uppercased())
                                .font(.system(.caption, design: .monospaced).weight(.bold))
                                .foregroundStyle(statusColor(status))
                            Spacer()
                            Text("\(sectionBooks.count)")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundStyle(NousTheme.dimText)
                        }
                    }
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(NousTheme.background)
        .overlay {
            if books.isEmpty {
                ConsoleEmptyState(
                    code: "READING_QUEUE_EMPTY",
                    detail: "Add a book with +. Swipe right to advance queued → reading → finished.")
            }
        }
        .safeAreaInset(edge: .bottom) {
            if finishedCount > 0 {
                Text("\(finishedCount) FINISHED ALL-TIME")
                    .font(.system(.caption2, design: .monospaced).weight(.semibold))
                    .foregroundStyle(NousTheme.dimText)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .background(NousTheme.background)
            }
        }
    }

    private func statusColor(_ status: BookStatus) -> Color {
        switch status {
        case .reading: return NousTheme.statusActive
        case .queued: return NousTheme.statusPending
        case .finished: return NousTheme.dimText
        }
    }
}

// MARK: - Row

private struct BookRow: View {
    let book: Book

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: book.status == .finished
                ? "book.closed.fill"
                : book.status == .reading ? "book.fill" : "book.closed")
                .foregroundStyle(book.status == .reading
                    ? NousTheme.statusActive : NousTheme.dimText)

            VStack(alignment: .leading, spacing: 3) {
                Text(book.title)
                    .strikethrough(book.status == .finished)
                    .foregroundStyle(book.status == .finished
                        ? NousTheme.dimText : NousTheme.textPrimary)
                    .font(.system(.subheadline, design: .monospaced))
                if !book.author.isEmpty {
                    Text(book.author.uppercased())
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(NousTheme.dimText)
                }
            }

            Spacer()

            if let finishedAt = book.finishedAt {
                Text(finishedAt.formatted(.dateTime.month(.abbreviated).day()))
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.dimText)
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(book.title)\(book.author.isEmpty ? "" : " by \(book.author)"), \(book.status.displayName)")
    }
}

// MARK: - New book sheet

private struct NewBookSheet: View {
    @Environment(\.dismiss) private var dismiss
    let defaultCategory: BookCategory

    @State private var title = ""
    @State private var author = ""
    @State private var status: BookStatus = .queued
    @State private var category: BookCategory

    init(defaultCategory: BookCategory) {
        self.defaultCategory = defaultCategory
        _category = State(initialValue: defaultCategory)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Title", text: $title)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                TextField("Author (optional)", text: $author)
                    .foregroundStyle(NousTheme.textPrimary)
                    .listRowBackground(NousTheme.panel)
                Picker("Category", selection: $category) {
                    ForEach(BookCategory.allCases, id: \.self) {
                        Text($0.displayName.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(NousTheme.panel)
                Picker("Status", selection: $status) {
                    ForEach(BookStatus.allCases, id: \.self) {
                        Text($0.displayName.uppercased()).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .listRowBackground(NousTheme.panel)
            }
            .scrollContentBackground(.hidden)
            .background(NousTheme.background)
            .navigationTitle("")
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("NEW BOOK")
                        .font(NousTheme.pixelFontBold(size: 14, relativeTo: .subheadline))
                        .tracking(2)
                        .foregroundStyle(NousTheme.onBackground)
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("CANCEL") { dismiss() }
                        .buttonStyle(.shell(NousTheme.alert))
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("ADD") { save() }
                        .buttonStyle(.shell(NousTheme.statusActive))
                        .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .toolbarBackground(NousTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private func save() {
        let book = Book(
            title: title.trimmingCharacters(in: .whitespaces),
            author: author.trimmingCharacters(in: .whitespaces),
            status: status,
            category: category)
        DataController.shared.addBook(book)
        dismiss()
    }
}

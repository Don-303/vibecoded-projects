//
//  WorkoutDayAssignment.swift
//  PersonalOps
//
//  One-off "do this plan today" override, independent of a plan's regular
//  scheduledWeekday. One row per calendar day — assigning a new plan for a
//  day replaces any existing assignment for that day.
//

import Foundation
import GRDB

struct WorkoutDayAssignment: Codable, Equatable, Identifiable, Sendable,
    FetchableRecord, PersistableRecord {

    static let databaseTableName = "workoutDayAssignment"

    var id: UUID
    var date: Date
    var workoutPlanId: UUID

    init(date: Date, workoutPlanId: UUID) {
        self.id = UUID()
        self.date = Calendar.current.startOfDay(for: date)
        self.workoutPlanId = workoutPlanId
    }
}

extension WorkoutDayAssignment {
    enum Columns {
        static let id = Column(CodingKeys.id)
        static let date = Column(CodingKeys.date)
        static let workoutPlanId = Column(CodingKeys.workoutPlanId)
    }
}

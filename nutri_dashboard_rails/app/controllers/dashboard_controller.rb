# frozen_string_literal: true

class DashboardController < ApplicationController
  def index
    @patients = Patient.order(:patient_name)
    @selected_patient = @patients.first
    if params[:patient_id].present?
      @selected_patient = Patient.find_by(id: params[:patient_id]) || @selected_patient
    end
  end
end

# frozen_string_literal: true

class PatientOrderOutChannel < ApplicationCable::Channel
  def subscribed
    patient_id = params[:patient_id].to_s
    stream_for patient_id if patient_id.present?
  end

  def unsubscribed
    stop_all_streams
  end
end

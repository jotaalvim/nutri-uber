# frozen_string_literal: true

require "net/http"
require "json"

class DashboardController < ApplicationController
  FOOD_FINDER_URL = ENV.fetch("FOOD_FINDER_URL", "http://127.0.0.1:5001")

  def index
    @patients = Patient.order(:patient_name)
    @selected_patient = @patients.first
    if params[:patient_id].present?
      @selected_patient = Patient.find_by(id: params[:patient_id]) || @selected_patient
    end
  end

  def cached_grocery_basket
    patient = Patient.find_by(id: params[:patient_id])
    return head :not_found unless patient

    uri = URI("#{FOOD_FINDER_URL}/cached_grocery_basket?patient_id=#{patient.id}&city=#{params[:city].presence || 'braga-norte'}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    response = http.get(uri)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    head :service_unavailable
  rescue StandardError
    head :internal_server_error
  end

  def cached_food
    patient = Patient.find_by(id: params[:patient_id])
    return head :not_found unless patient

    uri = URI("#{FOOD_FINDER_URL}/cached_food?patient_id=#{patient.id}&city=#{params[:city].presence || 'braga-norte'}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    response = http.get(uri)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    head :service_unavailable
  rescue StandardError
    head :internal_server_error
  end

  def warm_cache
    patient = Patient.find_by(id: params[:patient_id])
    return head :bad_request unless patient

    uri = URI("#{FOOD_FINDER_URL}/warm_cache")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient_id: patient.id,
      patient: patient_to_api_format(patient),
      city: params[:city].presence || "braga-norte"
    }.to_json

    response = http.request(request)
    render json: JSON.parse(response.body), status: response.code.to_i
  rescue Errno::ECONNREFUSED, SocketError
    render json: { status: "unavailable" }, status: :service_unavailable
  rescue StandardError
    render json: { status: "error" }, status: :internal_server_error
  end

  def grocery_basket
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    payload = patient_to_api_format(patient)
    uri = URI("#{FOOD_FINDER_URL}/grocery_basket")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 120

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient: payload,
      patient_id: patient.id,
      city: params[:city].presence || "braga-norte"
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: { error: body["error"] || "Grocery basket error" }, status: :unprocessable_entity
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: {
      error: "Food finder API not reachable at #{FOOD_FINDER_URL}. Start it with: python api.py"
    }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def add_basket_to_cart
    store_url = params[:store_url]
    items = params[:items] || []
    return render json: { error: "store_url and items required" }, status: :bad_request if store_url.blank? || items.empty?

    uri = URI("#{FOOD_FINDER_URL}/add_basket_to_cart")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 180

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      store_url: store_url,
      items: items.map { |i| { "name" => i["name"] } },
      keep_open: true
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: { error: "Food finder API not reachable" }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def add_to_food_log
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    items = params[:items] || []
    meal_type = params[:meal_type].presence || "Refeição"
    return render json: { error: "No items to add" }, status: :bad_request if items.empty?

    infos = patient.patient_infos || {}
    diary_key = "food_diary_history_and_obs"
    dietary = infos["dietary_history"] || infos.dig("dietary_history") || {}
    diary = dietary[diary_key] || dietary["food_diary_history_and_obs"] || []

    today = Time.zone.today.strftime("%Y-%m-%d")
    meals = items.map do |item|
      {
        "meal_type" => meal_type,
        "text" => [item["name"], item["restaurant"]].compact.join(" @ ")
      }
    end

    new_entry = {
      "date" => today,
      "meals" => meals,
      "observations" => "Ordered via Nutri-Uber basket"
    }

    existing_today = diary.find { |e| e["date"] == today }
    if existing_today
      existing_today["meals"] ||= []
      existing_today["meals"].concat(meals)
      existing_today["observations"] = [
        existing_today["observations"],
        "Ordered via Nutri-Uber basket"
      ].compact.join("\n")
    else
      diary << new_entry
    end

    infos["dietary_history"] ||= {}
    infos["dietary_history"][diary_key] = diary
    patient.update!(patient_infos: infos)

    render json: { status: "ok", message: "Added to food log", diary: diary }
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def find_food
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    payload = patient_to_api_format(patient)
    uri = URI("#{FOOD_FINDER_URL}/find_food")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 120

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient: payload,
      patient_id: patient.id,
      city: params[:city].presence || "braga-norte",
      max_restaurants: 3
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: { error: body["error"] || "Food finder error" }, status: :unprocessable_entity
    end
  rescue Errno::ECONNREFUSED, SocketError => e
    render json: {
      error: "Food finder API not reachable at #{FOOD_FINDER_URL}. Start it with: python api.py",
      hint: "If Rails runs in Docker, set FOOD_FINDER_URL=http://host.docker.internal:5001"
    }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def nutrition
    q = params[:q].to_s.strip
    return render json: { error: "q (query) required" }, status: :bad_request if q.blank?

    uri = URI("#{FOOD_FINDER_URL}/nutrition")
    uri.query = URI.encode_www_form(q: q)
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 8

    response = http.get(uri)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: { error: "Food finder API not reachable" }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  private

  def patient_to_api_format(patient)
    {
      patient_name: patient.patient_name,
      dee_goal: patient.dee_goal,
      dee_goal_unit: patient.dee_goal_unit,
      macronutrient_distribution_in_grams: {
        fat: patient.fat_grams,
        carbohydrate: patient.carbohydrate_grams,
        protein: patient.protein_grams
      },
      fiber_quantity_in_grams: patient.fiber_grams,
      patient_infos: patient.patient_infos || {}
    }
  end
end

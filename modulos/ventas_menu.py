# -*- coding: utf-8 -*-
from flask import Blueprint, render_template

ventas_bp = Blueprint("ventas_bp", __name__)

@ventas_bp.route("/ventas")
def ventas_menu():
    return render_template("ventas.html")

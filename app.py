import os
import csv
import io
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import and_
from functools import wraps
from datetime import datetime, timezone, timedelta

# --- App and Database Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'expenses.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
    expenses = db.relationship('Expense', backref='owner', lazy=True, cascade="all, delete-orphan")
    tags = db.relationship('Tag', backref='owner', lazy=True, cascade="all, delete-orphan")
    budget = db.relationship('Budget', backref='user', uselist=False, cascade="all, delete-orphan")

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(10), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    own_amount = db.Column(db.Float, nullable=False)
    tag = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receivables = db.relationship('Receivable', backref='expense', lazy=True, cascade="all, delete-orphan")

class Receivable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    person_name = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    is_paid = db.Column(db.Boolean, default=False, nullable=False)
    expense_id = db.Column(db.Integer, db.ForeignKey('expense.id'), nullable=False)

class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    period = db.Column(db.String(10), nullable=False, default='monthly') # Can be 'monthly' or 'weekly'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)

# --- User Authentication Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Main Routes ---
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/dashboard')
@login_required
def dashboard():
    user = db.session.get(User, session['user_id'])
    if not user:
        session.pop('user_id', None)
        flash('Your session was invalid. Please log in again.', 'error')
        return redirect(url_for('login'))

    user_expenses = sorted(user.expenses, key=lambda x: x.date, reverse=True)
    total_expenses = sum(expense.own_amount for expense in user_expenses)

    total_owed = db.session.query(db.func.sum(Receivable.amount)).join(Expense).filter(
        Expense.user_id == user.id,
        Receivable.is_paid == False
    ).scalar() or 0.0

    today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    user_tags = Tag.query.filter_by(user_id=user.id).all()
    available_tags = [tag.name for tag in user_tags]

    user_budget = user.budget
    budget_data = {'budget_obj': user_budget, 'spent': 0, 'remaining': 0, 'percent': 0}
    if user_budget:
        today = datetime.now(timezone.utc).date()
        if user_budget.period == 'weekly':
            start_of_period = today - timedelta(days=today.weekday()) # Monday
        else: # monthly
            start_of_period = today.replace(day=1)

        spent_this_period = db.session.query(db.func.sum(Expense.own_amount)).filter(
            Expense.user_id == user.id,
            Expense.date >= start_of_period.strftime('%Y-%m-%d')
        ).scalar() or 0.0
        
        budget_data['spent'] = spent_this_period
        budget_data['remaining'] = user_budget.amount - spent_this_period
        if user_budget.amount > 0:
            budget_data['percent'] = min(100, (spent_this_period / user_budget.amount) * 100)

    return render_template('index.html',
                           expenses=user_expenses,
                           total=total_expenses,
                           total_owed=total_owed,
                           user=user.username,
                           today_date=today_date,
                           available_tags=sorted(available_tags),
                           budget_data=budget_data)
        

@app.route('/reports')
@login_required
def reports():
    user = db.session.get(User, session['user_id'])
    if not user:
        session.pop('user_id', None)
        flash('Your session was invalid. Please log in again.', 'error')
        return redirect(url_for('login'))

    user_expenses = user.expenses
    chart_data = {}
    
    # Aggregate expenses by tag for the chart
    for expense in user_expenses:
        chart_data[expense.tag] = chart_data.get(expense.tag, 0) + expense.own_amount
    
    # Calculate budget overview data
    user_budget = user.budget
    budget_data = {'budget_obj': user_budget, 'spent': 0, 'remaining': 0, 'percent': 0}
    if user_budget:
        today = datetime.now(timezone.utc).date()
        if user_budget.period == 'weekly':
            start_of_period = today - timedelta(days=today.weekday())
        else:
            start_of_period = today.replace(day=1)

        spent_this_period = db.session.query(db.func.sum(Expense.own_amount)).filter(
            Expense.user_id == user.id,
            Expense.date >= start_of_period.strftime('%Y-%m-%d')
        ).scalar() or 0.0
        
        budget_data['spent'] = spent_this_period
        budget_data['remaining'] = user_budget.amount - spent_this_period
        if user_budget.amount > 0:
            budget_data['percent'] = min(100, (spent_this_period / user_budget.amount) * 100)
            
    return render_template('reports.html', 
                           user=user.username, 
                           chart_data=chart_data,
                           budget_data=budget_data)

# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user and user.password == password:
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'error')
        else:
            new_user = User(username=username, password=password)
            db.session.add(new_user)
            db.session.flush()

            default_tags = ["food", "college", "utilities", "transport", "other"]
            for tag_name in default_tags:
                new_tag = Tag(name=tag_name, user_id=new_user.id)
                db.session.add(new_tag)
                
            db.session.commit()
            flash('Account created successfully! Please login.', 'success')
            return redirect(url_for('login'))

    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))

# --- Expense and Split Routes ---
@app.route('/add', methods=['POST'])
@login_required
def add_expense():
    try:
        date = request.form.get('date')
        description = request.form.get('description')
        total_amount = float(request.form.get('amount'))
        tag = request.form.get('tag').strip().lower()
        user_id = session['user_id']
        is_split = 'is_split' in request.form
        
        if not tag:
            flash('Please select a tag for the expense.', 'error')
            return redirect(url_for('dashboard'))

        own_amount = total_amount
        new_expense = Expense(date=date, description=description, total_amount=total_amount, tag=tag, user_id=user_id, own_amount=own_amount)
        db.session.add(new_expense)

        if is_split:
            split_names = request.form.getlist('split_names[]')
            split_shares = request.form.getlist('split_shares[]')
            
            total_share_amount = 0
            for share_str in split_shares:
                total_share_amount += float(share_str)

            if total_share_amount >= total_amount:
                flash("Total of friends' shares cannot be greater than or equal to the total amount.", 'error')
                db.session.rollback()
                return redirect(url_for('dashboard'))
            
            new_expense.own_amount = total_amount - total_share_amount
            db.session.flush()

            for name, share_str in zip(split_names, split_shares):
                if name and share_str:
                    share = float(share_str)
                    if share <= 0:
                        flash("All shares must be positive amounts.", 'error')
                        db.session.rollback()
                        return redirect(url_for('dashboard'))
                    
                    new_receivable = Receivable(person_name=name, amount=share, is_paid=False, expense_id=new_expense.id)
                    db.session.add(new_receivable)
        
        db.session.commit()
        flash('Expense added successfully!', 'success')
    except (ValueError, TypeError):
        db.session.rollback()
        flash('Invalid amount entered. Please enter a valid number.', 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/delete/<int:id>')
@login_required
def delete_expense(id):
    expense = Expense.query.get_or_404(id)
    if expense.owner.id != session['user_id']:
        flash('You can only delete your own expenses.', 'error')
        return redirect(url_for('dashboard'))
    db.session.delete(expense)
    db.session.commit()
    flash('Expense deleted successfully.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/mark_paid/<int:receivable_id>', methods=['POST'])
@login_required
def mark_receivable_paid(receivable_id):
    receivable = Receivable.query.get_or_404(receivable_id)
    if receivable.expense.user_id != session['user_id']:
        flash('You do not have permission to modify this item.', 'error')
        return redirect(url_for('dashboard'))
    receivable.is_paid = True
    db.session.commit()
    flash(f"Payment from {receivable.person_name} marked as paid!", 'success')
    return redirect(url_for('dashboard'))

# --- Routes for Editing Expenses ---
@app.route('/expense/get/<int:expense_id>')
@login_required
def get_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != session['user_id']:
        return jsonify({'error': 'Unauthorized'}), 403

    receivables_data = []
    for r in expense.receivables:
        receivables_data.append({'person_name': r.person_name, 'amount': r.amount})

    return jsonify({
        'id': expense.id,
        'date': expense.date,
        'description': expense.description,
        'total_amount': expense.total_amount,
        'tag': expense.tag,
        'is_split': bool(expense.receivables),
        'receivables': receivables_data
    })

@app.route('/expense/edit/<int:expense_id>', methods=['POST'])
@login_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.user_id != session['user_id']:
        flash('You do not have permission to edit this expense.', 'error')
        return redirect(url_for('dashboard'))

    try:
        expense.date = request.form.get('date')
        expense.description = request.form.get('description')
        expense.total_amount = float(request.form.get('amount'))
        expense.tag = request.form.get('tag').strip().lower()
        is_split = 'is_split' in request.form
        
        # Clear existing receivables for this expense. We'll re-add them.
        for r in expense.receivables:
            db.session.delete(r)

        if is_split:
            split_names = request.form.getlist('split_names[]')
            split_shares = request.form.getlist('split_shares[]')
            
            total_share_amount = sum(float(s) for s in split_shares if s)

            if total_share_amount >= expense.total_amount:
                flash("Total of friends' shares cannot be greater than or equal to the total amount.", 'error')
                db.session.rollback()
                return redirect(url_for('dashboard'))

            expense.own_amount = expense.total_amount - total_share_amount
            
            for name, share_str in zip(split_names, split_shares):
                if name and share_str:
                    share = float(share_str)
                    if share <= 0:
                        flash("All shares must be positive amounts.", 'error')
                        db.session.rollback()
                        return redirect(url_for('dashboard'))
                    new_receivable = Receivable(person_name=name, amount=share, is_paid=False, expense_id=expense.id)
                    db.session.add(new_receivable)
        else:
             expense.own_amount = expense.total_amount

        db.session.commit()
        flash('Expense updated successfully!', 'success')
    except (ValueError, TypeError):
        db.session.rollback()
        flash('Invalid data provided. Please check the amounts.', 'error')
    return redirect(url_for('dashboard'))

# --- Tag Management API Routes ---
@app.route('/tags/add', methods=['POST'])
@login_required
def add_tag():
    tag_name = request.json.get('tag_name', '').strip().lower()
    user_id = session['user_id']
    if not tag_name: return jsonify({'success': False, 'message': 'Tag name cannot be empty.'})
    if Tag.query.filter_by(name=tag_name, user_id=user_id).first(): return jsonify({'success': False, 'message': f"Tag '{tag_name}' already exists."})
    db.session.add(Tag(name=tag_name, user_id=user_id))
    db.session.commit()
    all_tags = sorted([tag.name for tag in Tag.query.filter_by(user_id=user_id).all()])
    return jsonify({'success': True, 'message': f"Tag '{tag_name}' added.", 'tags': all_tags})

@app.route('/tags/delete', methods=['POST'])
@login_required
def delete_tag():
    tag_name = request.json.get('tag_name', '').strip().lower()
    user_id = session['user_id']
    
    if Expense.query.filter_by(tag=tag_name, user_id=user_id).first():
        return jsonify({'success': False, 'message': f"Cannot delete tag '{tag_name}' as it's currently in use."})

    tag_to_delete = Tag.query.filter_by(name=tag_name, user_id=user_id).first()
    if not tag_to_delete:
        return jsonify({'success': False, 'message': 'Tag not found.'})

    db.session.delete(tag_to_delete)
    db.session.commit()
    all_tags = sorted([tag.name for tag in Tag.query.filter_by(user_id=user_id).all()])
    return jsonify({'success': True, 'message': f"Tag '{tag_name}' deleted.", 'tags': all_tags})

# --- Budget Route ---
@app.route('/set_budget', methods=['POST'])
@login_required
def set_budget():
    try:
        amount_str = request.form.get('budget_amount')
        amount = float(amount_str) if amount_str else 0.0
        period = request.form.get('budget_period')
        user_id = session['user_id']
        
        if amount < 0 or period not in ['weekly', 'monthly']:
            flash('Invalid budget amount or period.', 'error')
            return redirect(url_for('dashboard'))

        budget = Budget.query.filter_by(user_id=user_id).first()

        if amount == 0.0: # Sentinel for removing budget
            if budget:
                db.session.delete(budget)
                db.session.commit()
                flash('Budget removed successfully!', 'success')
        else:
            if budget:
                budget.amount = amount
                budget.period = period
            else:
                budget = Budget(amount=amount, period=period, user_id=user_id)
                db.session.add(budget)
            db.session.commit()
            flash('Budget updated successfully!', 'success')
            
    except (ValueError, TypeError):
        db.session.rollback()
        flash('Invalid amount. Please enter a valid number.', 'error')
    
    return redirect(request.referrer or url_for('dashboard'))

# --- Comparison API Route ---
def get_period_data(user_id, start_date, end_date):
    """Helper function to get expense data for a given period."""
    expenses = Expense.query.filter(
        Expense.user_id == user_id,
        and_(Expense.date >= start_date, Expense.date <= end_date)
    ).all()

    total = sum(e.own_amount for e in expenses)
    by_tag = {}
    for e in expenses:
        by_tag[e.tag] = by_tag.get(e.tag, 0) + e.own_amount
    
    return {'total': total, 'by_tag': by_tag}

@app.route('/get_comparison_data')
@login_required
def get_comparison_data():
    user_id = session['user_id']
    
    p1_start = request.args.get('p1_start')
    p1_end = request.args.get('p1_end')
    p2_start = request.args.get('p2_start')
    p2_end = request.args.get('p2_end')

    if not all([p1_start, p1_end, p2_start, p2_end]):
        return jsonify({'error': 'Missing date parameters'}), 400

    period1_data = get_period_data(user_id, p1_start, p1_end)
    period2_data = get_period_data(user_id, p2_start, p2_end)

    return jsonify({
        'period1': period1_data,
        'period2': period2_data
    })


# --- CSV Upload Route ---
@app.route('/upload', methods=['POST'])
@login_required
def upload_csv():
    if 'csv_file' not in request.files:
        flash('No file part', 'error')
        return redirect(url_for('dashboard'))
    file = request.files['csv_file']
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(url_for('dashboard'))
    if file and file.filename.endswith('.csv'):
        try:
            stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
            csv_reader = csv.reader(stream)
            next(csv_reader, None) # Skip header row
            for row in csv_reader:
                if len(row) >= 4:
                    # Assuming format: Date, Description, Amount, Tag
                    new_expense = Expense(
                        date=row[0],
                        description=row[1],
                        total_amount=float(row[2]),
                        own_amount=float(row[2]), # Assuming CSV imports aren't split
                        tag=row[3].strip().lower(),
                        user_id=session['user_id']
                    )
                    db.session.add(new_expense)
            db.session.commit()
            flash('CSV file successfully imported!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {e}', 'error')
        return redirect(url_for('dashboard'))
    else:
        flash('Invalid file type. Please upload a .csv file.', 'error')
        return redirect(url_for('dashboard'))

# --- Main Execution ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)


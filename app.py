from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from functools import wraps 
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, desc, asc
from flask_paginate import get_page_parameter
from datetime import datetime, timedelta
import os
import re
import random
import pandas as pd
import json
import requests
from bs4 import BeautifulSoup
from collections import Counter
import time
from urllib.parse import quote

app = Flask(__name__)

# セッション設定
app.secret_key = "movie_admin_secret_key_1529"
app.permanent_session_lifetime = timedelta(hours=24)

# パスワード設定
ADMIN_PASSWORD = "1529"
SITE_ACCESS_PASSWORD = "imo4649"

def parse_date(s):
    try:
        return datetime.strptime(s, "%Y/%m/%d")
    except:
        return datetime.min 

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///movie_new.db'
app.config['SQLALCHEMY_BINDS'] = {
    'trending': 'sqlite:///trending_data.db'
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Movie(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.String)
    title = db.Column(db.String)
    revenue = db.Column(db.Float)
    year = db.Column(db.Integer)
    release_date = db.Column(db.String)
    category = db.Column(db.String)
    distributor = db.Column(db.String)
    description = db.Column(db.Text)
    director = db.Column(db.String)
    author = db.Column(db.String)
    actor = db.Column(db.String)
    scriptwriter = db.Column(db.String)
    producer = db.Column(db.String)
    copyright = db.Column(db.String)
    genre = db.Column(db.String)

# 新機能用のデータモデル
class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    excerpt = db.Column(db.String(300))
    author = db.Column(db.String(100))
    category = db.Column(db.String(50))
    tags = db.Column(db.String(200))
    image_url = db.Column(db.String(200))
    published_date = db.Column(db.DateTime, default=datetime.utcnow)
    view_count = db.Column(db.Integer, default=0)
    is_featured = db.Column(db.Boolean, default=False)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    response = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class TrendingData(db.Model):
    """SNS投稿数データを保存するテーブル"""
    __bind_key__ = 'trending'  # 専用データベースを指定
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.String(20), nullable=False)
    movie_title = db.Column(db.String(200), nullable=False)
    post_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

def site_access_required(f):
    """サイトアクセス認証デコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # サイトログインページは除外
        if request.endpoint == 'site_login':
            return f(*args, **kwargs)
        
        # サイトアクセス認証をチェック
        if not session.get('site_authenticated'):
            # 現在のURLを next パラメータとして保存
            return redirect(url_for('site_login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """管理者認証デコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_authenticated'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route("/site-login", methods=['GET', 'POST'])
def site_login():
    """サイトアクセス認証"""
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        
        if password == SITE_ACCESS_PASSWORD:
            session['site_authenticated'] = True
            session.permanent = True
            print("サイトアクセス認証成功")
            
            # 元々アクセスしようとしていたページにリダイレクト
            next_page = request.args.get('next') or request.form.get('next', url_for('search'))
            return redirect(next_page)
        else:
            print("サイトアクセス認証失敗: 間違ったパスワード")
            return render_template('site_login.html', error="パスワードが正しくありません")
    
    # 既に認証済みの場合でも、nextパラメータがあれば処理
    if session.get('site_authenticated'):
        next_page = request.args.get('next')
        if next_page:
            return redirect(next_page)
        return redirect(url_for('search'))
    
    return render_template('site_login.html')

@app.route("/admin/login", methods=['GET', 'POST'])
@site_access_required
def admin_login():
    """管理者ログイン"""
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        
        if password == ADMIN_PASSWORD:
            session['admin_authenticated'] = True
            session.permanent = True
            print("管理者ログイン成功")
            return redirect(url_for('admin_dashboard'))
        else:
            print("管理者ログイン失敗: 間違ったパスワード")
            return render_template('admin_login.html', error="パスワードが正しくありません")
    
    # 管理者認証済みでもログインページを表示（再認証可能にする）
    return render_template('admin_login.html')

@app.route("/admin/logout")
@site_access_required
def admin_logout():
    """管理者ログアウト"""
    session.pop('admin_authenticated', None)
    print("管理者ログアウト")
    return redirect(url_for('search'))

# 簡素化版 TrendingDataManager クラス
class TrendingDataManager:
    def __init__(self, app_instance):
        self.app = app_instance
        # MeCabの初期化（形態素解析用）
        try:
            import MeCab
            self.mecab = MeCab.Tagger('-Owakati')
        except:
            print("MeCab初期化失敗。pip install mecab-python3 が必要です")
            self.mecab = None
        
        print("TrendingDataManager初期化完了（データベース利用版）")
    
    def get_trending_by_date(self, target_date=None, limit=10):
        """指定日のトップ10トレンドデータを取得"""
        if target_date is None:
            # 利用可能な最新の日付を取得
            latest_date = db.session.query(TrendingData.date).order_by(desc(TrendingData.date)).first()
            if latest_date:
                target_date = latest_date[0]
            else:
                return []
        
        try:
            # TOP10のみ取得
            trending_list = TrendingData.query.filter_by(date=target_date)\
                                             .order_by(desc(TrendingData.post_count))\
                                             .limit(limit)\
                                             .all()
            
            if not trending_list:
                print(f"指定日 {target_date} のデータが見つかりません")
                return []
            
            result = []
            for i, trend in enumerate(trending_list, 1):
                # データベースから映画情報を検索
                movie_data = self.find_movie_in_database_enhanced(trend.movie_title)
                
                trend_info = {
                    'rank': i,
                    'title': trend.movie_title,
                    'post_count': trend.post_count,
                    'date': trend.date,
                    'movie_data': movie_data,
                    'change': self.calculate_change(trend.movie_title, target_date),
                    'trend_score': min(100, (trend.post_count / max(1, trending_list[0].post_count)) * 100),
                    'word_cloud': None  # 後で設定
                }
                result.append(trend_info)
            
            # 上位3位のワードクラウドを生成（簡略版）
            for i in range(min(3, len(result))):
                result[i]['word_cloud'] = self.generate_fallback_wordcloud(result[i]['title'])
            
            print(f"{target_date} のトレンドデータ取得: {len(result)} 件")
            return result
            
        except Exception as e:
            print(f"トレンドデータ取得エラー: {e}")
            return []
    
    def find_movie_in_database_enhanced(self, title):
        """映画データベースから映画情報を検索"""
        try:
            # 1. 完全一致検索
            movie = Movie.query.filter(Movie.title == title).first()
            if movie:
                return self.format_movie_data(movie)
            
            # 2. 部分一致検索
            movie = Movie.query.filter(Movie.title.contains(title)).first()
            if movie:
                return self.format_movie_data(movie)
            
            # 3. 逆方向の部分一致検索
            movies = Movie.query.all()
            for movie in movies:
                if movie.title and (movie.title in title or title in movie.title):
                    return self.format_movie_data(movie)
            
            return None
            
        except Exception as e:
            print(f"映画検索エラー ({title}): {e}")
            return None
    
    def format_movie_data(self, movie):
        """映画データを整形"""
        return {
            'id': movie.id,
            'movie_id': movie.movie_id,
            'title': movie.title,
            'revenue': movie.revenue,
            'year': movie.year,
            'director': movie.director,
            'category': movie.category,
            'poster_path': f"posters/{movie.movie_id}.jpg" if movie.movie_id else None
        }
    
    def generate_fallback_wordcloud(self, movie_title):
        """フォールバック用ワードクラウド（映画タイトルベース）"""
        word_templates = {
            '鬼滅の刃': ['感動', '泣いた', '最高', '劇場版', 'アニメ', '無限列車', '炭治郎', '禰豆子'],
            'ミッション': ['アクション', 'トム・クルーズ', 'スタント', 'ハラハラ', '迫力', 'スパイ'],
            'スーパーマン': ['ヒーロー', 'DC', 'アメコミ', '飛行', '正義', 'クラーク'],
            'ルパン': ['次元', '五右衛門', '不二子', '銭形', '泥棒', 'カリオストロ'],
            'しんちゃん': ['家族', 'しんのすけ', 'ひろし', 'みさえ', '春日部', '面白い'],
            'アンパンマン': ['アンパンマン', '子供', 'ばいきんまん', 'ジャムおじさん', '正義'],
            'ジュラシック': ['恐竜', 'ティラノサウルス', 'ラプトル', 'パーク', '迫力'],
            'BAD BOYS': ['アクション', 'コメディ', 'マイアミ', 'バディ', '爆発'],
            'スティッチ': ['ディズニー', 'ハワイ', '家族', 'かわいい', '感動']
        }
        
        # 映画タイトルに含まれるキーワードでマッチング
        matched_words = ['面白い', '最高', '感動', '泣いた', '笑った', '見た', '良かった', 'おすすめ']
        
        for key, words in word_templates.items():
            if key in movie_title:
                matched_words = words
                break
        
        # フォーマット
        keywords = []
        colors = ['#1a73e8', '#34a853', '#ea4335', '#fbbc04', '#9c27b0', '#ff6f00']
        
        for i, word in enumerate(matched_words[:8]):
            keywords.append({
                'text': word,
                'size': max(14, 24 - (i * 2)),
                'color': colors[i % len(colors)],
                'count': 10 - i
            })
        
        return keywords
    
    def calculate_change(self, title, current_date):
        """前日との変化率を計算"""
        try:
            # 日付の形式を統一
            current_date_obj = datetime.strptime(current_date, '%Y/%m/%d')
            prev_date_obj = current_date_obj - timedelta(days=1)
            prev_date = prev_date_obj.strftime('%Y/%m/%d')
            
            current_data = TrendingData.query.filter_by(date=current_date, movie_title=title).first()
            prev_data = TrendingData.query.filter_by(date=prev_date, movie_title=title).first()
            
            if current_data and prev_data and prev_data.post_count > 0:
                change_rate = ((current_data.post_count - prev_data.post_count) / prev_data.post_count) * 100
                if change_rate > 0:
                    return f"+{change_rate:.1f}%"
                else:
                    return f"{change_rate:.1f}%"
            elif current_data and not prev_data:
                return "NEW"
            
            return "-%"
            
        except Exception as e:
            print(f"変化率計算エラー ({title}): {e}")
            return "-%"
    
    def get_available_dates(self):
        """利用可能な日付一覧を取得"""
        try:
            dates = db.session.query(TrendingData.date).distinct().order_by(desc(TrendingData.date)).all()
            return [date[0] for date in dates]
        except Exception as e:
            print(f"日付取得エラー: {e}")
            return []

# データベース初期化関数
def init_database():
    """データベースとサンプルデータを初期化"""
    with app.app_context():
        db.create_all()
        
        # サンプル記事データ
        if Article.query.count() == 0:
            sample_articles = [
                Article(
                    title="SNS投稿数から見る映画トレンド分析",
                    content="Yahoo!リアルタイム検索のデータを基に、SNS投稿数と映画の人気度の関係を分析します。投稿数が多い映画ほど話題性が高く、興行収入との相関も見られます。",
                    excerpt="SNS投稿数データから読み解く映画トレンドの最新動向",
                    author="データアナリスト 田中太郎",
                    category="映画分析",
                    tags="SNS,投稿数,トレンド,データ分析",
                    is_featured=True
                ),
                Article(
                    title="リアルタイム検索で見る映画の話題性",
                    content="リアルタイムでの検索数や投稿数は、映画の実際の人気を反映する重要な指標です。従来の興行収入データと合わせて分析することで、より深い洞察が得られます。",
                    excerpt="リアルタイムデータが示す映画人気の新しい指標",
                    author="映画マーケティング専門家 佐藤花子",
                    category="トレンド",
                    tags="リアルタイム,検索,投稿数,人気度"
                )
            ]
            
            for article in sample_articles:
                db.session.add(article)
            
            try:
                db.session.commit()
                print("サンプル記事データを作成しました")
            except Exception as e:
                db.session.rollback()
                print(f"記事データの作成に失敗: {e}")

# グローバルインスタンス
trending_manager = None

def init_trending_manager():
    """トレンドマネージャーの初期化（データベース利用版）"""
    global trending_manager
    print("TrendingDataManager を初期化中...")
    trending_manager = TrendingDataManager(app)
    print("TrendingDataManager の初期化完了")

# AIチャットボット機能
class MovieAnalysisBot:
    def __init__(self):
        self.responses = {
            "投稿数": [
                "SNS投稿数データを見ると、リアルタイムで話題の映画がわかりますね。投稿数が多い作品ほど注目度が高い傾向があります。",
                "Yahoo!リアルタイム検索のデータから、どの映画が今話題になっているかを分析できます。"
            ],
            "興行収入": [
                "興行収入とSNS投稿数には相関関係があることが多いです。話題性が高い映画ほど劇場に足を運ぶ人が多くなります。",
                "投稿数ランキング上位の映画は、興行収入でも好成績を残すことが多いですね。"
            ],
            "トレンド": [
                "リアルタイムのSNS投稿数から見ると、今最も話題になっている映画がわかります。",
                "投稿数の推移を見ることで、映画の人気の変化も追跡できます。"
            ],
            "ランキング": [
                "投稿数ランキングでは、リアルタイムで話題の映画が上位に来ます。興行収入ランキングとは違った視点で人気を見ることができます。",
                "SNS投稿数ランキングは、特に若年層に人気の作品が上位に来やすい傾向があります。"
            ]
        }
    
    def get_response(self, user_message):
        user_message = user_message.lower()
        
        for keyword, responses in self.responses.items():
            if keyword in user_message:
                return random.choice(responses)
        
        return "SNS投稿数データと映画データベースを組み合わせて、様々な角度から映画を分析できます。どんなことが知りたいですか？"

# ===== ルート定義 =====

@app.route("/api/search-suggestions")
@site_access_required
def search_suggestions():
    query_type = request.args.get('type')
    term = request.args.get('term', '').strip()
    
    if not term or len(term) < 2:
        return jsonify([])
    
    suggestions = []
    
    try:
        if query_type == 'title':
            results = Movie.query.filter(Movie.title.contains(term)).limit(10).all()
            suggestions = [movie.title for movie in results]
        elif query_type == 'director':
            results = db.session.query(Movie.director).filter(
                Movie.director.contains(term), Movie.director.isnot(None)
            ).distinct().limit(10).all()
            suggestions = [r[0] for r in results if r[0]]
        elif query_type == 'actor':
            results = db.session.query(Movie.actor).filter(
                Movie.actor.contains(term), Movie.actor.isnot(None)
            ).distinct().limit(10).all()
            suggestions = [r[0] for r in results if r[0]]
        elif query_type == 'distributor':
            results = db.session.query(Movie.distributor).filter(
                Movie.distributor.contains(term), Movie.distributor.isnot(None)
            ).distinct().limit(10).all()
            suggestions = [r[0] for r in results if r[0]]
        elif query_type == 'genre':  # ジャンル検索候補追加
            results = db.session.query(Movie.genre).filter(
                Movie.genre.contains(term), Movie.genre.isnot(None)
            ).distinct().limit(10).all()
            # ジャンルは複数あるため、分割して処理
            genre_set = set()
            for r in results:
                if r[0]:
                    for genre in r[0].split(','):
                        genre = genre.strip()
                        if term.lower() in genre.lower():
                            genre_set.add(genre)
            suggestions = list(genre_set)[:10]
    except Exception as e:
        print(f"検索候補取得エラー: {e}")
        suggestions = []
    
    return jsonify(suggestions[:10])

@app.route("/")
@app.route("/search")
@site_access_required
def search():
    query = Movie.query

    # 基本検索パラメータ
    title = request.args.get('title')
    director = request.args.get('director')
    actor = request.args.get('actor')
    distributor = request.args.get('distributor')
    category = request.args.get('category')
    min_revenue = request.args.get('min_revenue')
    max_revenue = request.args.get('max_revenue')
    
    # 新しい検索パラメータ
    years = request.args.getlist('years')
    genres = request.args.getlist('genres')
    year_match = request.args.get('year_match', 'any')
    genre_match = request.args.get('genre_match', 'any')
    
    order_by = request.args.get('order_by', 'revenue')
    sort = request.args.get('sort', 'desc')

    # 基本フィルタリング
    if title:
        query = query.filter(Movie.title.contains(title))
    if director:
        query = query.filter(Movie.director.contains(director))
    if actor:
        query = query.filter(Movie.actor.contains(actor))
    if distributor:
        # 配給会社のエイリアス処理
        groups = [
            ['WB', 'ワーナー', 'ワーナー・ブラザース映画'],
            ['SPE', 'ソニー・ピクチャーズエンタテインメント'],
            ['BV', 'WDS', 'ウォルト・ディズニー・ジャパン', 'ブエナビスタ', 'ディズニー']
        ]

        distributor_aliases = {}
        for group in groups:
            for term in group:
                distributor_aliases[term.upper()] = group

        patterns = distributor_aliases.get(distributor.upper(), [distributor])
        conditions = []
        for pattern in patterns:
            conditions.extend([
                Movie.distributor == pattern,
                Movie.distributor.like(f"%、{pattern}、%"),
                Movie.distributor.like(f"{pattern}、%"),
                Movie.distributor.like(f"%、{pattern}"),
                Movie.distributor.like(f"%{pattern}%")
            ])
        query = query.filter(or_(*conditions))

    if category:
        query = query.filter(Movie.category == category)
    
    if min_revenue:
        query = query.filter(Movie.revenue >= float(min_revenue))
    if max_revenue:
        query = query.filter(Movie.revenue <= float(max_revenue))

    # 年検索の処理
    if years:
        year_conditions = []
        if year_match == 'range' and len(years) >= 2:
            # 範囲指定の場合
            min_year = min([int(y) for y in years])
            max_year = max([int(y) for y in years])
            query = query.filter(Movie.year >= min_year, Movie.year <= max_year)
        else:
            # 指定年のいずれかの場合
            year_conditions = [Movie.year == int(year) for year in years]
            query = query.filter(or_(*year_conditions))

    # ジャンル検索の処理
    if genres:
        genre_conditions = []
        for genre in genres:
            genre_conditions.append(
                or_(
                    Movie.genre.like(f"%{genre}%"),
                    Movie.genre.like(f"{genre},%"),
                    Movie.genre.like(f"%, {genre},%"),
                    Movie.genre.like(f"%, {genre}"),
                    Movie.genre == genre
                )
            )
        
        if genre_match == 'all':
            # AND検索（完全一致）
            for condition in genre_conditions:
                query = query.filter(condition)
        else:
            # OR検索（一部一致）
            query = query.filter(or_(*genre_conditions))

    movies = query.all()

    # ソート処理
    if movies:
        if order_by == 'release_date':
            movies.sort(key=lambda m: parse_date(m.release_date), reverse=(sort == 'desc'))
        elif order_by == 'genre':
            movies.sort(key=lambda m: m.genre or '', reverse=(sort == 'desc'))
        elif hasattr(Movie, order_by):
            sample = getattr(movies[0], order_by, '')
            if isinstance(sample, (float, int)):
                movies.sort(key=lambda m: getattr(m, order_by) if getattr(m, order_by) is not None else 0, reverse=(sort == 'desc'))
            else:
                movies.sort(key=lambda m: getattr(m, order_by) or '', reverse=(sort == 'desc'))

    # ページネーション
    page = request.args.get(get_page_parameter(), type=int, default=1)
    per_page = 20
    start = (page - 1) * per_page
    end = start + per_page
    pagination_items = movies[start:end]

    total_pages = (len(movies) + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages

    args_no_page = request.args.to_dict()
    args_no_page.pop("page", None)

    return render_template('search.html',
        movies=pagination_items,
        pagination={
            'page': page,
            'pages': total_pages,
            'has_prev': has_prev,
            'has_next': has_next,
            'prev_num': page - 1,
            'next_num': page + 1
        },
        order_by=order_by,
        sort=sort,
        args_no_page=args_no_page,
        total_results=len(movies)
    )

@app.route("/movie/<int:movie_id>")
@site_access_required
def movie_detail(movie_id):
    movie = Movie.query.filter_by(id=movie_id).first_or_404()
    return render_template("movie_detail.html", movie=movie)

@app.route("/table")
@site_access_required
def table_view():
    query = Movie.query

    # 基本検索パラメータ
    title = request.args.get('title')
    director = request.args.get('director')
    actor = request.args.get('actor')
    distributor = request.args.get('distributor')
    category = request.args.get('category')
    min_revenue = request.args.get('min_revenue')
    max_revenue = request.args.get('max_revenue')
    
    # 新しい検索パラメータ
    years = request.args.getlist('years')
    genres = request.args.getlist('genres')
    year_match = request.args.get('year_match', 'any')
    genre_match = request.args.get('genre_match', 'any')
    
    order_by = request.args.get('order_by', 'release_date')
    sort = request.args.get('sort', 'desc')

    # 基本フィルタリング（search関数と同じロジック）
    if title:
        query = query.filter(Movie.title.contains(title))
    if director:
        query = query.filter(Movie.director.contains(director))
    if actor:
        query = query.filter(Movie.actor.contains(actor))
    if distributor:
        query = query.filter(
            or_(
                Movie.distributor == distributor,
                Movie.distributor.like(f"%、{distributor}、%"),
                Movie.distributor.like(f"{distributor}、%"),
                Movie.distributor.like(f"%、{distributor}"),
                Movie.distributor.like(f"%{distributor}%")
            )
        )
    if category:
        query = query.filter(Movie.category == category)
    if min_revenue:
        query = query.filter(Movie.revenue >= float(min_revenue))
    if max_revenue:
        query = query.filter(Movie.revenue <= float(max_revenue))

    # 年検索の処理
    if years:
        if year_match == 'range' and len(years) >= 2:
            min_year = min([int(y) for y in years])
            max_year = max([int(y) for y in years])
            query = query.filter(Movie.year >= min_year, Movie.year <= max_year)
        else:
            year_conditions = [Movie.year == int(year) for year in years]
            query = query.filter(or_(*year_conditions))

    # ジャンル検索の処理
    if genres:
        genre_conditions = []
        for genre in genres:
            genre_conditions.append(
                or_(
                    Movie.genre.like(f"%{genre}%"),
                    Movie.genre.like(f"{genre},%"),
                    Movie.genre.like(f"%, {genre},%"),
                    Movie.genre.like(f"%, {genre}"),
                    Movie.genre == genre
                )
            )
        
        if genre_match == 'all':
            for condition in genre_conditions:
                query = query.filter(condition)
        else:
            query = query.filter(or_(*genre_conditions))

    # ソート処理
    if order_by == 'release_date':
        movies = query.all()
        movies.sort(key=lambda m: parse_date(m.release_date), reverse=(sort == 'desc'))
    elif order_by == 'genre':
        movies = query.all()
        movies.sort(key=lambda m: m.genre or '', reverse=(sort == 'desc'))
    elif hasattr(Movie, order_by):
        column = getattr(Movie, order_by)
        if sort == 'asc':
            query = query.order_by(asc(column))
        else:
            query = query.order_by(desc(column))
        movies = query.all()
    else:
        movies = query.all()

    # ページネーション
    page = request.args.get(get_page_parameter(), type=int, default=1)
    per_page = 50
    start = (page - 1) * per_page
    end = start + per_page
    pagination_items = movies[start:end]

    total_pages = (len(movies) + per_page - 1) // per_page
    has_prev = page > 1
    has_next = page < total_pages

# args_no_page を追加
    args_no_page = request.args.to_dict()
    args_no_page.pop("page", None)

    return render_template("table_view.html", 
        movies=pagination_items, 
        order_by=order_by, 
        sort=sort,
        pagination={
            'page': page,
            'pages': total_pages,
            'has_prev': has_prev,
            'has_next': has_next,
            'prev_num': page - 1,
            'next_num': page + 1
        },
        args_no_page=args_no_page,
        total_results=len(movies)
    )
# ===== 新機能のルート =====

@app.route("/articles")
@site_access_required
def articles():
    page = request.args.get('page', 1, type=int)
    category = request.args.get('category')
    
    query = Article.query
    if category:
        query = query.filter(Article.category == category)
    
    try:
        articles = query.order_by(desc(Article.published_date)).paginate(
            page=page, per_page=10, error_out=False
        )
    except Exception as e:
        print(f"記事取得エラー: {e}")
        from flask_sqlalchemy import Pagination
        articles = Pagination(page=page, per_page=10, total=0, items=[])
    
    categories = ['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
    
    return render_template('articles.html', articles=articles, categories=categories, current_category=category)

@app.route("/articles/<int:article_id>")
@site_access_required
def article_detail(article_id):
    article = Article.query.get_or_404(article_id)
    article.view_count += 1
    db.session.commit()
    
    related_articles = Article.query.filter(
        Article.category == article.category,
        Article.id != article.id
    ).limit(3).all()
    
    return render_template('article_detail.html', article=article, related_articles=related_articles)

@app.route("/chat")
@site_access_required
def movie_chat():
    return render_template('movie_chat.html')

@app.route("/api/chat", methods=['POST'])
@site_access_required
def chat_api():
    try:
        data = request.get_json()
        user_message = data.get('message', '')
        session_id = data.get('session_id', 'default')
        
        bot = MovieAnalysisBot()
        response = bot.get_response(user_message)
        
        try:
            chat_message = ChatMessage(
                session_id=session_id,
                message=user_message,
                response=response
            )
            db.session.add(chat_message)
            db.session.commit()
        except Exception as e:
            print(f"チャット履歴保存エラー: {e}")
            db.session.rollback()
        
        return jsonify({
            'response': response,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        print(f"チャットAPIエラー: {e}")
        return jsonify({
            'response': '申し訳ございません。一時的にサービスを利用できません。',
            'timestamp': datetime.now().isoformat()
        }), 500

# ===== SNSトレンド機能（強化版） =====

@app.route("/trending")
@site_access_required
def sns_ranking():
    """SNSランキングページ（データベース利用版）"""
    global trending_manager
    
    # 日付パラメータの取得
    selected_date = request.args.get('date')
    
    if trending_manager is None:
        return render_template('sns_ranking.html', 
                             trending_movies=[], 
                             available_dates=[], 
                             selected_date=None,
                             error="トレンドマネージャーが初期化されていません。")
    
    # 利用可能な日付を取得
    available_dates = trending_manager.get_available_dates()
    
    # デフォルトは最新の日付
    if selected_date is None and available_dates:
        selected_date = available_dates[0]
    
    # TOP10のトレンドデータを取得
    trending_movies = trending_manager.get_trending_by_date(selected_date, limit=10)
    
    return render_template('sns_ranking.html', 
                         trending_movies=trending_movies,
                         available_dates=available_dates,
                         selected_date=selected_date)

@app.route("/api/trending-update")
@site_access_required
def trending_update():
    """トレンドデータ更新API"""
    global trending_manager
    
    selected_date = request.args.get('date')
    
    if trending_manager is None:
        return jsonify({'error': 'トレンドマネージャーが初期化されていません'})
    
    trending_movies = trending_manager.get_trending_by_date(selected_date, limit=10)
    return jsonify(trending_movies)

@app.route("/api/word-cloud/<movie_title>")
@site_access_required
def word_cloud_api(movie_title):
    """ワードクラウドAPI（フォールバック版）"""
    global trending_manager
    
    if trending_manager is None:
        return jsonify([])
    
    # フォールバック用ワードクラウドを生成
    word_cloud_data = trending_manager.generate_fallback_wordcloud(movie_title)
    return jsonify(word_cloud_data)

# ===== 記事管理機能 =====

@app.route("/admin")
@site_access_required
@admin_required
def admin_dashboard():
    """管理者ダッシュボード"""
    try:
        # 記事統計
        total_articles = Article.query.count()
        featured_articles = Article.query.filter_by(is_featured=True).count()
        recent_articles = Article.query.filter(
            Article.published_date >= datetime.now() - timedelta(days=7)
        ).count()
        
        # カテゴリ別統計
        category_stats = db.session.query(
            Article.category,
            db.func.count(Article.id).label('count')
        ).group_by(Article.category).all()
        
        # 最近の記事
        recent_articles_list = Article.query.order_by(
            desc(Article.published_date)
        ).limit(5).all()
        
        return render_template('admin_dashboard.html',
            total_articles=total_articles,
            featured_articles=featured_articles,
            recent_articles=recent_articles,
            category_stats=category_stats,
            recent_articles_list=recent_articles_list
        )
    except Exception as e:
        print(f"管理画面エラー: {e}")
        return render_template('admin_dashboard.html',
            total_articles=0,
            featured_articles=0,
            recent_articles=0,
            category_stats=[],
            recent_articles_list=[]
        )

@app.route("/admin/articles")
@site_access_required
@admin_required
def admin_articles():
    """記事一覧管理"""
    page = request.args.get('page', 1, type=int)
    category = request.args.get('category')
    search = request.args.get('search', '').strip()
    
    query = Article.query
    
    if category:
        query = query.filter(Article.category == category)
    
    if search:
        query = query.filter(
            or_(
                Article.title.contains(search),
                Article.content.contains(search),
                Article.author.contains(search)
            )
        )
    
    try:
        articles = query.order_by(desc(Article.published_date)).paginate(
            page=page, per_page=10, error_out=False
        )
    except Exception as e:
        print(f"記事一覧取得エラー: {e}")
        from flask_sqlalchemy import Pagination
        articles = Pagination(page=page, per_page=10, total=0, items=[])
    
    categories = ['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
    
    return render_template('admin_articles.html', 
        articles=articles, 
        categories=categories,
        current_category=category,
        search_term=search
    )

@app.route("/admin/articles/new", methods=['GET', 'POST'])
@site_access_required
@admin_required
def admin_create_article():
    """新規記事作成"""
    if request.method == 'POST':
        try:
            # フォームデータを取得
            title = request.form.get('title', '').strip()
            content = request.form.get('content', '').strip()
            excerpt = request.form.get('excerpt', '').strip()
            author = request.form.get('author', '').strip()
            category = request.form.get('category', '').strip()
            tags = request.form.get('tags', '').strip()
            is_featured = request.form.get('is_featured') == 'on'
            
            # バリデーション
            if not title or not content:
                return render_template('admin_article_form.html', 
                    error="タイトルと内容は必須です",
                    form_data=request.form,
                    categories=['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
                )
            
            # 抜粋が空の場合は自動生成
            if not excerpt:
                excerpt = content[:200] + '...' if len(content) > 200 else content
            
            # 新しい記事を作成
            article = Article(
                title=title,
                content=content,
                excerpt=excerpt,
                author=author or '管理者',
                category=category,
                tags=tags,
                is_featured=is_featured,
                published_date=datetime.now()
            )
            
            db.session.add(article)
            db.session.commit()
            
            print(f"新規記事作成: {title}")
            return redirect(url_for('admin_articles'))
            
        except Exception as e:
            print(f"記事作成エラー: {e}")
            db.session.rollback()
            return render_template('admin_article_form.html', 
                error=f"記事の作成に失敗しました: {str(e)}",
                form_data=request.form,
                categories=['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
            )
    
    # GET リクエストの場合は作成フォームを表示
    categories = ['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
    return render_template('admin_article_form.html', 
        categories=categories,
        article=None  # 新規作成なのでNone
    )

@app.route("/admin/articles/<int:article_id>/edit", methods=['GET', 'POST'])
@site_access_required
@admin_required
def admin_edit_article(article_id):
    """記事編集"""
    article = Article.query.get_or_404(article_id)
    
    if request.method == 'POST':
        try:
            # フォームデータを取得
            article.title = request.form.get('title', '').strip()
            article.content = request.form.get('content', '').strip()
            article.excerpt = request.form.get('excerpt', '').strip()
            article.author = request.form.get('author', '').strip()
            article.category = request.form.get('category', '').strip()
            article.tags = request.form.get('tags', '').strip()
            article.is_featured = request.form.get('is_featured') == 'on'
            
            # バリデーション
            if not article.title or not article.content:
                return render_template('admin_article_form.html', 
                    error="タイトルと内容は必須です",
                    article=article,
                    categories=['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
                )
            
            # 抜粋が空の場合は自動生成
            if not article.excerpt:
                article.excerpt = article.content[:200] + '...' if len(article.content) > 200 else article.content
            
            db.session.commit()
            
            print(f"記事更新: {article.title}")
            return redirect(url_for('admin_articles'))
            
        except Exception as e:
            print(f"記事更新エラー: {e}")
            db.session.rollback()
            return render_template('admin_article_form.html', 
                error=f"記事の更新に失敗しました: {str(e)}",
                article=article,
                categories=['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
            )
    
    # GET リクエストの場合は編集フォームを表示
    categories = ['映画分析', '興行収入', 'トレンド', 'インタビュー', 'レビュー', '業界動向']
    return render_template('admin_article_form.html', 
        categories=categories,
        article=article
    )

@app.route("/admin/articles/<int:article_id>/delete", methods=['POST'])
@site_access_required
@admin_required
def admin_delete_article(article_id):
    """記事削除"""
    try:
        article = Article.query.get_or_404(article_id)
        title = article.title
        
        db.session.delete(article)
        db.session.commit()
        
        print(f"記事削除: {title}")
        return jsonify({'success': True, 'message': '記事を削除しました'})
        
    except Exception as e:
        print(f"記事削除エラー: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'削除に失敗しました: {str(e)}'})

@app.route("/admin/articles/<int:article_id>/toggle-featured", methods=['POST'])
@site_access_required
@admin_required
def admin_toggle_featured(article_id):
    """注目記事の切り替え"""
    try:
        article = Article.query.get_or_404(article_id)
        article.is_featured = not article.is_featured
        
        db.session.commit()
        
        status = "注目記事に設定" if article.is_featured else "注目記事を解除"
        print(f"{article.title}: {status}")
        
        return jsonify({
            'success': True, 
            'message': f'{status}しました',
            'is_featured': article.is_featured
        })
        
    except Exception as e:
        print(f"注目記事切り替えエラー: {e}")
        db.session.rollback()
        return jsonify({'success': False, 'message': f'切り替えに失敗しました: {str(e)}'})


if __name__ == "__main__":
    print("映画データベース アプリケーション起動中...")
    
    # データベースのテーブルを最初に作成
    with app.app_context():
        db.create_all()
        print("データベーステーブル作成完了")
    
    # データベース初期化
    init_database()
    
    # トレンドマネージャー初期化（データベース利用版）
    init_trending_manager()
    
    print("初期化完了！ブラウザで http://localhost:5000 にアクセスしてください")
    app.run(host='0.0.0.0', port=5000, debug=True)